# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Provisioning for the reference integration.

Two things happen here, and the distinction matters:

* **Workspace onboarding** creates the application's own rows, and creates the
  Mind Palace corpora the workspace will use. Creating a corpus is
  administrative -- a tenant-scoped credential cannot do it, and there is no HTTP
  route that can -- so it happens through `mindpalace corpus create`, the
  documented operator command, by a process that already holds the maintenance
  database role. This is the one part of the setup that is not a request-handler
  operation, and it is deliberately the part no request handler performs.
* **Per-workspace Mind Palace credentials** are then issued for exactly the
  corpora that workspace owns, through `mindpalace credential issue` and the same
  documented command. From that point on nothing in the workflow constructs a
  corpus name from client input; it resolves from the rows created here, and Mind
  Palace checks it against the credential.

Both commands are invoked as subprocesses rather than imported. That is the point
of the example: an integrating application uses Mind Palace's published surface,
so this file uses the published surface too, and a test can assert that nothing
here reaches past it. An earlier version imported `api.services.corpora` and
`api.services.authz` directly, which worked and proved nothing about whether an
independent application could do the same.

Run it with `python demo.py`, or import `provision` from a test.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from marketing_memory.memory import MemoryClient, http_client
from marketing_memory.service import hash_key
from marketing_memory.tenancy import (
    Campaign,
    Membership,
    User,
    Workspace,
    add_campaign,
    add_membership,
    add_user,
    add_workspace,
    brand_corpus,
    campaign_corpus,
    create_schema,
)

ORGANISATIONS = {
    "alpha": Workspace(id="ws-alpha", name="Alpha Field Gear"),
    "beta": Workspace(id="ws-beta", name="Beta Home Networks"),
}

ROLES = ("workspace_admin", "content_creator", "reviewer")


@dataclass
class Provisioned:
    """What the demo and the tests need, and nothing that leaks at runtime."""

    user_keys: dict[str, str] = field(default_factory=dict)
    workspace_keys: dict[str, str] = field(default_factory=dict)
    campaigns: dict[str, Campaign] = field(default_factory=dict)
    #: One HTTP client for the whole run, so the demo and the tests share it.
    http: object = None


async def provision(db, base_url: str) -> Provisioned:
    """Create the schema, two organisations, their users and their credentials.

    Every workspace gets its own Mind Palace credential covering exactly its own
    corpora, issued here because granting access is an administrative operation.
    A client that does not hold one cannot reach that workspace's memory at all,
    which is what makes the isolation assertions mean something.

    Everything after the corpora exist goes over HTTP to a running Mind Palace,
    through the real middleware and the real authorization path, so the seeding
    below proves the credential works rather than assuming it.
    """
    out = Provisioned()
    await create_schema(db)
    out.http = http_client(base_url)

    for workspace in ORGANISATIONS.values():
        await add_workspace(db, workspace)
        for campaign_name in ("spring", "monsoon"):
            name = f"{workspace.name} {campaign_name}"
            create_corpus(campaign_corpus(workspace.id, name), f"{name} campaign memory")
            out.campaigns[f"{workspace.id}:{campaign_name}"] = Campaign(
                id=f"{workspace.id}-{campaign_name}",
                workspace_id=workspace.id,
                name=name,
                brief=(
                    f"Announce the {campaign_name} range to "
                    f"{workspace.name}'s existing customers."
                ),
                objective="awareness",
            )
            await add_campaign(db, out.campaigns[f"{workspace.id}:{campaign_name}"])
        create_corpus(brand_corpus(workspace.id), f"{workspace.name} brand memory")

        # One credential per workspace, covering exactly its own corpora.
        # Every later call is scoped by this, and by nothing the client says.
        scoped = [
            brand_corpus(workspace.id),
            *[
                campaign_corpus(workspace.id, campaign.name)
                for campaign in out.campaigns.values()
                if campaign.workspace_id == workspace.id
            ],
        ]
        out.workspace_keys[workspace.id] = issue_credential(f"service-{workspace.id}", scoped)

        for role in ROLES:
            user = User(id=f"{workspace.id}-{role}", email=f"{role}@{workspace.id}.example")
            secret = secrets.token_urlsafe(24)
            await add_user(db, user, hash_key(secret))
            out.user_keys[f"{workspace.id}:{role}"] = secret
            await add_membership(
                db, Membership(user_id=user.id, workspace_id=workspace.id, role=role)
            )

    # Seed authoritative memory: approved brand facts, and one fact per campaign.
    # These are the only facts any generated asset may draw on. Always the
    # workspace's own credential, in either mode: it is what makes these writes
    # legal rather than unscoped.
    for workspace in ORGANISATIONS.values():
        client = MemoryClient(out.workspace_keys[workspace.id], client=out.http)
        brand = brand_corpus(workspace.id)
        for key, statement in BRAND_FACTS[workspace.id].items():
            await client.remember(brand, statement, key=key)
        for campaign in out.campaigns.values():
            if campaign.workspace_id != workspace.id:
                continue
            corpus = campaign_corpus(workspace.id, campaign.name)
            for key, statement in CAMPAIGN_FACTS[(workspace.id, campaign.name)].items():
                await client.remember(corpus, statement, key=key)

    return out


def _mindpalace(*arguments: str) -> str:
    """Run a documented `mindpalace` operator command and return its stdout.

    A subprocess, deliberately. An integrating application does not import Mind
    Palace's Python modules to create a corpus -- it runs the operator command, or
    asks whoever holds the maintenance role to. Keeping that shape here means the
    example cannot quietly grow a dependency the way a real integration would.
    """
    result = subprocess.run(
        [sys.executable, "-m", "cli.main", *arguments],
        capture_output=True,
        text=True,
        cwd=_repository_root(),
        env=os.environ,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`mindpalace {' '.join(arguments)}` failed ({result.returncode}):\n"
            f"{result.stderr[-2000:]}"
        )
    return result.stdout


def _repository_root() -> str:
    """The Mind Palace checkout, so the CLI runs against the same database."""
    return str(Path(__file__).resolve().parents[3])


def create_corpus(name: str, description: str = "") -> None:
    """Create a corpus through the documented operator command.

    Idempotent, because onboarding gets re-run: the command exits non-zero for a
    corpus that already exists, and that is a success here.
    """
    existing = subprocess.run(
        [sys.executable, "-m", "cli.main", "corpus", "create", name, "-d", description],
        capture_output=True,
        text=True,
        cwd=_repository_root(),
        env=os.environ,
        timeout=300,
    )
    if existing.returncode != 0 and "exists" not in (existing.stdout + existing.stderr).lower():
        raise RuntimeError(f"`mindpalace corpus create {name}` failed:\n{existing.stderr[-2000:]}")


def issue_credential(name: str, corpora: list[str], role: str = "read_write") -> str:
    """Issue a credential scoped to `corpora`, and return its secret."""
    arguments = [name]
    for corpus in corpora:
        arguments += ["--corpus", corpus]
    return _mindpalace("credential", "issue", *arguments, "--role", role, "--quiet").strip()


BRAND_FACTS = {
    "ws-alpha": {
        "battery_life": "ALPHA_BRAND the Kestrel battery lasts 41 hours.",
        "warranty": "ALPHA_BRAND every Kestrel carries a three-year warranty.",
    },
    "ws-beta": {
        "mesh_bands": "BETA_BRAND the Nimbus router has four mesh bands.",
        "warranty": "BETA_BRAND every Nimbus carries a two-year warranty.",
    },
}

CAMPAIGN_FACTS = {
    ("ws-alpha", "Alpha Field Gear spring"): {
        "audience": "ALPHA_CAMPAIGN_SPRING the spring range targets coastal schools.",
    },
    ("ws-alpha", "Alpha Field Gear monsoon"): {
        "audience": "ALPHA_CAMPAIGN_MONSOON the monsoon range targets hill stations.",
    },
    ("ws-beta", "Beta Home Networks spring"): {
        "audience": "BETA_CAMPAIGN_SPRING the spring push targets new homeowners.",
    },
    ("ws-beta", "Beta Home Networks monsoon"): {
        "audience": "BETA_CAMPAIGN_MONSOON the monsoon push targets small flats.",
    },
}

#: A claim with no approved source. It is offered to the workflow to prove that
#: an unsupported claim does not become approved memory by being requested.
UNAPPROVED_CLAIM = "The Kestrel is certified by every aviation authority worldwide."
