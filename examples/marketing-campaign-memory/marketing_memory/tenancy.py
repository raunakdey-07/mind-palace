# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The application's own records: workspaces, memberships, campaigns.

This is the data a marketing application has anyway -- who the user is, which
workspace they belong to, what role they hold there, which campaigns exist and
what state their content is in. It is stored in the application's own schema,
next to Mind Palace's tables and never mixed with them.

The important part is `corpus_for_workspace` and `corpus_for_campaign`. A client
never supplies a corpus name. The application resolves one from these rows, so a
request that carries `corpus=someone-elses` has to name a corpus the server
already decided for that user -- and when it does not, nothing resolves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Role = Literal["workspace_admin", "content_creator", "reviewer"]

ROLE_CREATOR: Role = "content_creator"
ROLE_REVIEWER: Role = "reviewer"
ROLE_ADMIN: Role = "workspace_admin"
ROLES: tuple[Role, ...] = (ROLE_ADMIN, ROLE_CREATOR, ROLE_REVIEWER)

#: Four platform variants, one corpus-scoped campaign asset each. Enough to show
#: that a campaign's memory holds together across formats without becoming a
#: shared namespace.
PLATFORM_VARIANTS = ("blog", "linkedin", "facebook", "instagram")

SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return SLUG_UNSAFE.sub("-", text.casefold()).strip("-")


def brand_corpus(workspace_id: str) -> str:
    """The corpus holding one workspace's brand memory.

    `workspace_id` is the application's own identifier, so the corpus name is
    derived from it rather than from anything a request supplies.
    """
    return f"{slug(workspace_id)}-brand"


def campaign_corpus(workspace_id: str, campaign_name: str) -> str:
    """The corpus holding one campaign's own memory.

    Both parts are inputs to the name, and both come from the caller's own
    records. Two campaigns of the same workspace therefore never collide, and
    two workspaces' campaigns never share a corpus even when they discuss the
    same product.
    """
    return f"{slug(workspace_id)}-c-{slug(campaign_name)}"


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str


@dataclass(frozen=True)
class User:
    id: str
    email: str


@dataclass(frozen=True)
class Membership:
    """A user's verified membership of one workspace, with their role."""

    user_id: str
    workspace_id: str
    role: Role
    status: str = "active"


@dataclass(frozen=True)
class Campaign:
    id: str
    workspace_id: str
    name: str
    brief: str
    objective: str = "awareness"


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS marketing_app;

CREATE TABLE IF NOT EXISTS marketing_app.workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marketing_app.users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    key_hash CHAR(64) NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS marketing_app.memberships (
    user_id TEXT NOT NULL REFERENCES marketing_app.users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL
        REFERENCES marketing_app.workspaces(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('workspace_admin','content_creator','reviewer')),
    status TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (user_id, workspace_id)
);
CREATE TABLE IF NOT EXISTS marketing_app.campaigns (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL
        REFERENCES marketing_app.workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    brief TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT 'awareness',
    UNIQUE (workspace_id, name)
);
CREATE TABLE IF NOT EXISTS marketing_app.assets (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES marketing_app.campaigns(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    -- The assertion-bearing text, without the provenance footer. Stored
    -- separately because "every sentence in this asset is backed by an approved
    -- fact" is an audit question about the asset, not about the draft that
    -- happened to produce it.
    claims TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK (state IN ('draft','in_review','approved','rejected')),
    reviewer_note TEXT,
    -- The brand and campaign memory versions the draft was generated from, so a
    -- later auditor can see what the generator was actually given.
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (campaign_id, platform, revision)
);
CREATE TABLE IF NOT EXISTS marketing_app.decisions (
    id BIGSERIAL PRIMARY KEY,
    -- Keyed to the asset *lineage*, not to one revision: each transition creates
    -- a new asset id, and an audit trail that only survives on the current
    -- revision would lose the record of what happened to the previous ones.
    campaign_id TEXT NOT NULL REFERENCES marketing_app.campaigns(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    note TEXT,
    revision INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS decisions_lineage
    ON marketing_app.decisions (campaign_id, platform, id);
"""


async def create_schema(db: AsyncSession) -> None:
    """Idempotent. An application's schema is not a Mind Palace migration."""
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            await db.execute(text(statement))
    await db.commit()


class Denied(Exception):
    """The server refused. The frontend is never what decides this."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def add_workspace(db: AsyncSession, workspace: Workspace) -> None:
    await db.execute(
        text(
            "INSERT INTO marketing_app.workspaces (id, name) VALUES (:id, :name) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": workspace.id, "name": workspace.name},
    )
    await db.commit()


async def add_user(db: AsyncSession, user: User, key_hash: str) -> None:
    """Create or re-key a user.

    An upsert rather than `DO NOTHING`, because the example provisions fresh
    demo credentials on every run: with `DO NOTHING` a second run would return a
    secret that was never stored, and every later call would be refused as
    "not authenticated". Re-running a demo must be able to reset it.
    """
    await db.execute(
        text(
            "INSERT INTO marketing_app.users (id, email, key_hash) "
            "VALUES (:id, :email, :hash) "
            "ON CONFLICT (id) DO UPDATE SET key_hash = excluded.key_hash"
        ),
        {"id": user.id, "email": user.email, "hash": key_hash},
    )
    await db.commit()


async def add_membership(db: AsyncSession, membership: Membership) -> None:
    """Idempotent, and it *updates* the role and status.

    Re-running provisioning re-asserts the intended membership rather than
    failing on a duplicate, so a role change is an ordinary re-run and a revoked
    membership can be restored deliberately.
    """
    await db.execute(
        text(
            "INSERT INTO marketing_app.memberships (user_id, workspace_id, role, status) "
            "VALUES (:u, :w, :r, :s) "
            "ON CONFLICT (user_id, workspace_id) DO UPDATE "
            "SET role = excluded.role, status = excluded.status"
        ),
        {
            "u": membership.user_id,
            "w": membership.workspace_id,
            "r": membership.role,
            "s": membership.status,
        },
    )
    await db.commit()


async def add_campaign(db: AsyncSession, campaign: Campaign) -> None:
    """Idempotent, so re-running an example does not need a clean database."""
    await db.execute(
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
    await db.commit()


async def membership_for(db: AsyncSession, key_hash: str, workspace_id: str):
    """Resolve a presented credential to a *verified membership*.

    The key identifies the user; the row identifies what they may do. Nothing
    about the workspace is taken from the request: a caller cannot ask to be a
    member of a workspace by claiming to be.
    """
    row = (
        await db.execute(
            text(
                "SELECT m.user_id, m.role, m.status FROM marketing_app.memberships m "
                "JOIN marketing_app.users u ON u.id = m.user_id "
                "WHERE u.key_hash = :hash AND m.workspace_id = :workspace"
            ),
            {"hash": key_hash, "workspace": workspace_id},
        )
    ).first()
    return row
