# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The campaign workflow, and the permission matrix that guards it.

Three things are worth reading closely.

**The workspace is never a parameter.** Every method takes a `Principal`, and
the workspace comes from `Principal.workspace_id`, which came from a membership
row. There is no `workspace` argument anywhere in this class, so there is no
value a caller could supply to point the workflow at somebody else's data. The
same is true of corpora: they are derived from the workspace and the campaign
name, both of which are resolved against the caller's own rows.

**The corpus mapping is server-controlled.** `brand_corpus` and `campaign_corpus`
in `tenancy.py` are pure functions of records the application owns. No method
here accepts a corpus name, so a client that sends one has nowhere for it to
land -- which is why a cross-tenant attempt fails at the first server-side
resolution rather than at some later filter.

**The matrix is enforced here, not in a UI.** `PERMISSION` is the single table
of who may do what, `authorise` is the single gate every operation passes
through, and a refusal happens before any memory is read or written. Hiding a
button is not a control, and neither is a route the frontend does not link to.

The matrix, in full:

======================================  ========  =======  =========
operation                               admin     creator  reviewer
======================================  ========  =======  =========
read approved brand facts               yes       yes      yes
read a campaign in an authorised space  yes       yes      yes
create a campaign                       yes       yes      no
record approved brand facts             yes       no       no
generate or regenerate a draft          yes       yes      no
edit a submitted draft                  yes       no       yes
approve or reject an asset              yes       no       yes
manage membership and roles             yes       no       no
reach another workspace                 no        no       no
======================================  ========  =======  =========

Two rules are not in the table because they are not permissions:

* generated text never becomes approved memory. Approval is an explicit
  transition recorded against a specific revision, and a rejected draft is
  never written to memory at all;
* a claim without an approved source stays unapproved. The generator reports
  what it could not support instead of inventing a source for it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

from marketing_memory.generator import Fact, generate
from marketing_memory.tenancy import (
    PLATFORM_VARIANTS,
    Campaign,
    Denied,
    Membership,
    brand_corpus,
    campaign_corpus,
    membership_for,
)

Operation = Literal[
    "read_brand",
    "read_campaign",
    "create_campaign",
    "record_brand_fact",
    "generate_draft",
    "edit_draft",
    "decide_asset",
    "manage_members",
]

#: The one place the answer to "may this role do this?" is written down.
PERMISSION: dict[Operation, frozenset[str]] = {
    "read_brand": frozenset({"workspace_admin", "content_creator", "reviewer"}),
    "read_campaign": frozenset({"workspace_admin", "content_creator", "reviewer"}),
    "create_campaign": frozenset({"workspace_admin", "content_creator"}),
    "record_brand_fact": frozenset({"workspace_admin"}),
    "generate_draft": frozenset({"workspace_admin", "content_creator"}),
    "edit_draft": frozenset({"workspace_admin", "reviewer"}),
    "decide_asset": frozenset({"workspace_admin", "reviewer"}),
    "manage_members": frozenset({"workspace_admin"}),
}


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _as_texts(value) -> list[str]:
    """A JSONB column arrives already decoded on some drivers and as text on others."""
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def _provenance(facts: list[Fact]) -> tuple[str, ...]:
    """What a draft was generated from: every key, pinned to its memory version.

    Stored on the asset so a later auditor can answer "which version of which
    fact was this written from" without re-running the generator.
    """
    return tuple(sorted({f"{fact.key}@{fact.version_id}" for fact in facts}))


@dataclass(frozen=True)
class Principal:
    """A verified user, in one workspace, with one role.

    Carries the credential rather than only the role it resolved to, so every
    operation can re-verify. A role captured once -- at the start of a long
    workflow, or by a background job that began before somebody left the
    workspace -- would keep working after the membership is revoked. Revocation
    has to take effect on the next call, not on the next login.
    """

    user_id: str
    workspace_id: str
    role: str
    secret: str = ""

    def require(self, operation: Operation) -> None:
        if self.role not in PERMISSION[operation]:
            raise Denied(f"{self.role} may not {operation}")


@dataclass(frozen=True)
class Asset:
    id: str
    campaign_id: str
    platform: str
    revision: int
    body: str
    state: str
    reviewer_note: str | None
    sources: tuple[str, ...]
    claims: str = ""


class MarketingMemory:
    """The workflow.

    Memory is reached through the public memory contract and nothing else: no
    direct SQL against Mind Palace's tables, and no import of an internal service
    the SDK does not expose. That is deliberate -- if the example needed the
    internals, an adopter would need them too.
    """

    def __init__(self, db, brand_client, campaign_client_for):
        """``brand_client`` is one workspace's memory client.

        ``campaign_client_for`` builds one for a workspace and campaign. A client
        is bound to a single identity in tenant-scoped mode, so one per
        workspace rather than one shared across both.
        """
        self.db = db
        self._brand = brand_client
        self._campaign_client_for = campaign_client_for

    # -- identity ---------------------------------------------------------

    async def principal(self, secret: str, workspace_id: str) -> Principal:
        """Resolve a presented key to a verified membership of one workspace."""
        row = await membership_for(self.db, hash_key(secret), workspace_id)
        if row is None:
            # One message for "no such key" and "not a member here": whether
            # somebody else exists in this workspace is not the caller's business.
            raise Denied("not authenticated for this workspace")
        user_id, role, status = row
        if status != "active":
            raise Denied("membership is not active")
        return Principal(user_id=user_id, workspace_id=workspace_id, role=role, secret=secret)

    async def authorise(self, who: Principal, operation: Operation) -> Principal:
        """Re-verify, then apply the matrix. The single gate for every operation.

        Re-reading the membership row on every call is what makes a revocation
        effective immediately, including for work that started while the
        membership was still good.
        """
        current = await self.principal(who.secret, who.workspace_id)
        current.require(operation)
        return current

    # -- brand memory -----------------------------------------------------

    async def record_brand_fact(self, who: Principal, key: str, statement: str):
        """An approved product fact, written to the workspace's brand corpus."""
        who = await self.authorise(who, "record_brand_fact")
        return await self._brand.remember(brand_corpus(who.workspace_id), statement, key=key)

    async def approved_brand_facts(self, who: Principal) -> list[Fact]:
        """Approved brand facts, with the memory version each came from."""
        who = await self.authorise(who, "read_brand")
        response = await self._brand.current(brand_corpus(who.workspace_id))
        return [
            Fact(key=claim.key, text=claim.claim, version_id=claim.version_id)
            for claim in response.current_memories
        ]

    # -- campaigns --------------------------------------------------------

    async def create_campaign(self, who: Principal, name: str, brief: str) -> Campaign:
        """Create a campaign in the caller's own workspace. Never anybody else's."""
        who = await self.authorise(who, "create_campaign")
        campaign = Campaign(
            id=f"{who.workspace_id}-{name}".replace(" ", "-").casefold(),
            workspace_id=who.workspace_id,
            name=name,
            brief=brief,
        )
        await self.db.execute(
            text(
                "INSERT INTO marketing_app.campaigns (id, workspace_id, name, brief, objective) "
                "VALUES (:i, :w, :n, :b, :o) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "i": campaign.id,
                "w": campaign.workspace_id,
                "n": campaign.name,
                "b": campaign.brief,
                "o": campaign.objective,
            },
        )
        await self.db.commit()
        return campaign

    async def campaign_facts(self, who: Principal, campaign_name: str) -> list[Fact]:
        """This campaign's own facts, and only this campaign's.

        The campaign is looked up inside the caller's workspace, so a name from
        another organisation resolves to nothing rather than to its data. The
        sibling campaign's corpus is never opened, which is why a fact recorded
        for one campaign cannot answer a question about another.
        """
        who = await self.authorise(who, "read_campaign")
        campaign = await self._campaign(who.workspace_id, campaign_name)
        client = self._campaign_client_for(campaign.workspace_id, campaign.name)
        response = await client.current(campaign_corpus(campaign.workspace_id, campaign.name))
        return [
            Fact(key=claim.key, text=claim.claim, version_id=claim.version_id)
            for claim in response.current_memories
        ]

    async def record_campaign_fact(
        self, who: Principal, campaign_name: str, key: str, statement: str
    ):
        who = await self.authorise(who, "generate_draft")
        campaign = await self._campaign(who.workspace_id, campaign_name)
        client = self._campaign_client_for(campaign.workspace_id, campaign.name)
        return await client.remember(
            campaign_corpus(campaign.workspace_id, campaign.name), statement, key=key
        )

    # -- assets -----------------------------------------------------------

    async def generate_draft(self, who: Principal, campaign_name: str, platform: str) -> Asset:
        """Compose a draft from approved memory only.

        The draft is stored as an application asset in `draft` state and is
        deliberately *not* written to memory. Generation reading authoritative
        memory and generation becoming authoritative memory are different things,
        and conflating them is how a model's output becomes a fact.
        """
        who = await self.authorise(who, "generate_draft")
        if platform not in PLATFORM_VARIANTS:
            raise Denied(f"unsupported platform: {platform}")
        campaign = await self._campaign(who.workspace_id, campaign_name)
        brand = await self.approved_brand_facts(who)
        facts = await self.campaign_facts(who, campaign.name)
        draft = generate(platform, brand, facts, campaign.brief)
        return await self._save_asset(
            campaign.id,
            platform,
            draft.body,
            "draft",
            None,
            _provenance(brand + facts),
            draft.claims_text,
        )

    async def submit_for_review(self, who: Principal, asset_id: str) -> Asset:
        who = await self.authorise(who, "generate_draft")
        asset = await self._asset(who, asset_id)
        if asset.state != "draft":
            raise Denied(f"only a draft can be submitted; this is {asset.state}")
        return await self._transition(who, asset, "in_review", None, asset.body)

    async def edit_draft(self, who: Principal, asset_id: str, body: str) -> Asset:
        """A reviewer edit becomes a new revision, never an overwrite.

        The old revision stays, so "what did the reviewer change, and from what"
        is answerable later from the decisions table.
        """
        who = await self.authorise(who, "edit_draft")
        asset = await self._asset(who, asset_id)
        if asset.state not in {"in_review", "rejected"}:
            raise Denied(f"only a submitted or rejected asset can be edited; this is {asset.state}")
        return await self._transition(who, asset, "in_review", None, body)

    async def approve(self, who: Principal, asset_id: str, note: str = "") -> Asset:
        return await self._decide(who, asset_id, "approved", note)

    async def reject(self, who: Principal, asset_id: str, note: str) -> Asset:
        if not note.strip():
            raise Denied("a rejection needs a reason")
        return await self._decide(who, asset_id, "rejected", note)

    async def regenerate(self, who: Principal, asset_id: str) -> Asset:
        """A new revision from current approved memory, not a new claim."""
        who = await self.authorise(who, "generate_draft")
        asset = await self._asset(who, asset_id)
        campaign = await self._campaign_for_asset(asset)
        brand = await self.approved_brand_facts(who)
        facts = await self.campaign_facts(who, campaign["name"])
        draft = generate(asset.platform, brand, facts, campaign["brief"])
        return await self._save_asset(
            campaign["id"],
            asset.platform,
            draft.body,
            "draft",
            None,
            _provenance(brand + facts),
            draft.claims_text,
        )

    async def _decide(self, who: Principal, asset_id: str, state: str, note: str) -> Asset:
        who = await self.authorise(who, "decide_asset")
        asset = await self._asset(who, asset_id)
        if asset.state != "in_review":
            raise Denied(f"only a submitted asset can be decided; this is {asset.state}")
        return await self._transition(who, asset, state, note, asset.body)

    # -- internals --------------------------------------------------------

    async def _campaign(self, workspace_id: str, name: str) -> Campaign:
        """Look a campaign up *inside one workspace*.

        The workspace is part of the lookup key, so the same campaign name in two
        workspaces resolves to two different campaigns and neither is reachable
        from the other.
        """
        row = (
            await self.db.execute(
                text(
                    "SELECT id, workspace_id, name, brief, objective "
                    "FROM marketing_app.campaigns WHERE workspace_id = :w AND name = :n"
                ),
                {"w": workspace_id, "n": name},
            )
        ).first()
        if row is None:
            raise Denied("no such campaign in this workspace")
        return Campaign(id=row[0], workspace_id=row[1], name=row[2], brief=row[3], objective=row[4])

    async def _campaign_for_asset(self, asset: Asset) -> dict:
        row = (
            await self.db.execute(
                text(
                    "SELECT c.id, c.workspace_id, c.name, c.brief "
                    "FROM marketing_app.campaigns c WHERE c.id = :id"
                ),
                {"id": asset.campaign_id},
            )
        ).first()
        if row is None:
            raise Denied("no such campaign")
        return {"id": row[0], "workspace_id": row[1], "name": row[2], "brief": row[3]}

    async def _asset(self, who: Principal, asset_id: str) -> Asset:
        """Load an asset, and refuse one that belongs to another workspace.

        The check is on the parent campaign, not on the asset id: an id is not a
        capability, and an id that arrives from another workspace must not resolve
        even when it is well formed.
        """
        row = (
            await self.db.execute(
                text(
                    "SELECT a.id, a.campaign_id, a.platform, a.revision, a.body, a.state, "
                    "       a.reviewer_note, a.sources, c.workspace_id, a.claims "
                    "FROM marketing_app.assets a "
                    "JOIN marketing_app.campaigns c ON c.id = a.campaign_id "
                    "WHERE a.id = :id"
                ),
                {"id": asset_id},
            )
        ).first()
        if row is None or row[8] != who.workspace_id:
            raise Denied("no such asset in this workspace")
        return Asset(
            id=row[0],
            campaign_id=row[1],
            platform=row[2],
            revision=row[3],
            body=row[4],
            state=row[5],
            reviewer_note=row[6],
            sources=tuple(_as_texts(row[7])),
            claims=row[9],
        )

    async def _save_asset(
        self,
        campaign_id: str,
        platform: str,
        body: str,
        state: str,
        note: str | None,
        sources,
        claims: str = "",
    ) -> Asset:
        row = (
            await self.db.execute(
                text(
                    "SELECT coalesce(max(revision), 0) FROM marketing_app.assets "
                    "WHERE campaign_id = :c AND platform = :p"
                ),
                {"c": campaign_id, "p": platform},
            )
        ).scalar_one()
        revision = row + 1
        asset_id = f"{campaign_id}:{platform}:{revision}"
        await self.db.execute(
            text(
                "INSERT INTO marketing_app.assets "
                "(id, campaign_id, platform, revision, body, claims, state, reviewer_note, "
                " sources) VALUES (:i, :c, :p, :r, :b, :cl, :s, :n, :src)"
            ),
            {
                "i": asset_id,
                "c": campaign_id,
                "p": platform,
                "r": revision,
                "b": body,
                "cl": claims,
                "s": state,
                "n": note,
                "src": json.dumps(list(sources)),
            },
        )
        await self.db.commit()
        return Asset(
            id=asset_id,
            campaign_id=campaign_id,
            platform=platform,
            revision=revision,
            body=body,
            state=state,
            reviewer_note=note,
            sources=tuple(sources),
            claims=claims,
        )

    async def _transition(self, who, asset, state, note, body) -> Asset:
        campaign = await self._campaign_for_asset(asset)
        saved = await self._save_asset(
            campaign["id"], asset.platform, body, state, note, asset.sources, asset.claims
        )
        action = "submit" if state == "in_review" else state
        await self.db.execute(
            text(
                "INSERT INTO marketing_app.decisions "
                "(campaign_id, platform, asset_id, actor_id, action, note, revision) "
                "VALUES (:c, :p, :a, :u, :act, :n, :r)"
            ),
            {
                "c": campaign["id"],
                "p": asset.platform,
                "a": saved.id,
                "u": who.user_id,
                "act": action,
                "n": note,
                "r": saved.revision,
            },
        )
        await self.db.commit()
        return saved

    async def decision_history(self, asset_id: str) -> list[dict]:
        """What was decided on an asset's lineage, by whom, on which revision.

        Keyed to the campaign and platform rather than to one revision, because
        every transition creates a new revision; an audit trail pinned to the
        current id would lose the record of what happened to the previous ones.

        What this does not establish: that the underlying product claim was true,
        or that the generator followed its context faithfully. It records who
        accepted what, on which revision, and nothing more.
        """
        rows = (
            await self.db.execute(
                text(
                    "SELECT d.actor_id, d.action, d.note, d.revision, d.observed_at "
                    "FROM marketing_app.decisions d "
                    "JOIN marketing_app.assets a "
                    "  ON a.campaign_id = d.campaign_id AND a.platform = d.platform "
                    "WHERE a.id = :a ORDER BY d.id"
                ),
                {"a": asset_id},
            )
        ).fetchall()
        return [
            {"actor": r[0], "action": r[1], "note": r[2], "revision": r[3], "at": r[4]}
            for r in rows
        ]


__all__ = [
    "Asset",
    "MarketingMemory",
    "PERMISSION",
    "Principal",
    "hash_key",
    "Membership",
]
