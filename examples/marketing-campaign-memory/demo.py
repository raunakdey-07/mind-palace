# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Run the reference integration end to end. Deterministic, offline, no model.

    python examples/marketing-campaign-memory/demo.py

Needs a PostgreSQL with the schema applied (`alembic upgrade head`). For the
isolation half of the demonstration, also run it with

    MIND_PALACE_AUTH_MODE=required

so that a cross-organisation read is refused rather than served. The demo prints
which mode it is in, because a demo that quietly runs permissively is worse than
no demo.

Every memory call here goes over HTTP, through the real middleware, the real
authorization choke point and the real database policies. Only the socket is
missing, because the Mind Palace application is mounted in-process rather than
started as a service -- so this exercises the same path a deployment would, with
no port to configure.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from marketing_memory.memory import MemoryClient  # noqa: E402
from marketing_memory.provision import provision  # noqa: E402
from marketing_memory.service import MarketingMemory  # noqa: E402
from marketing_memory.tenancy import Denied  # noqa: E402

ALPHA = "ws-alpha"
BETA = "ws-beta"
PLATFORMS = ("blog", "linkedin", "facebook", "instagram")


def say(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


async def _authorization_mode(base_url: str) -> str:
    """Which authorization mode the service is actually in.

    Read from `/health/ready`, which is public and reports it for exactly this
    reason: a deployment must not be tenant-safe by accident. Asking the service
    rather than reading the environment means the demo cannot describe a mode the
    service is not in.
    """
    from marketing_memory.memory import http_client

    async with http_client(base_url) as client:
        response = await client.get("/health/ready")
    body = response.json()
    # `503` carries no `auth`: the service is up and refusing traffic because the
    # database role is not enforcing its policies. Saying so is more useful than
    # a KeyError, and it is the one deployment mistake this demo can make.
    if response.status_code != 200:
        raise SystemExit(f"Mind Palace at {base_url} is not ready: " f"{body.get('checks', body)}")
    return body["auth"]["mode"]


async def main() -> int:
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    # Where Mind Palace is running. This demo is a *client* of Mind Palace: it
    # imports none of it, not even the ASGI application object, because the thing
    # this example has to demonstrate is that an independent application can be
    # built on the documented interface and nothing else.
    base_url = os.environ.get("MIND_PALACE_URL", "http://127.0.0.1:8000")

    mode = await _authorization_mode(base_url)
    print(f"authorization mode: {mode} (at {base_url})")

    # The application's own database, opened here rather than imported: a host
    # application connects to its own store and knows nothing about Mind Palace's.
    # The driver is normalised because a deployment may hand the same DSN to a
    # sync and an async process.
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        seeded = await provision(db, base_url)
        service = MarketingMemory(
            db,
            brand_client=MemoryClient(seeded.workspace_keys[ALPHA], client=seeded.http),
            campaign_client_for=lambda workspace, name: MemoryClient(
                seeded.workspace_keys[workspace], client=seeded.http
            ),
        )

        creator = await service.principal(seeded.user_keys[f"{ALPHA}:content_creator"], ALPHA)
        reviewer = await service.principal(seeded.user_keys[f"{ALPHA}:reviewer"], ALPHA)
        beta_creator = await service.principal(seeded.user_keys[f"{BETA}:content_creator"], BETA)
        spring = seeded.campaigns[f"{ALPHA}:spring"].name
        monsoon = seeded.campaigns[f"{ALPHA}:monsoon"].name

        say("Approved brand facts, reusable by every campaign in the workspace")
        for fact in await service.approved_brand_facts(creator):
            print(f"  {fact.key}: {fact.text}")

        say("Campaign facts stay inside their own campaign")
        for fact in await service.campaign_facts(creator, spring):
            print(f"  spring:  {fact.text}")
        for fact in await service.campaign_facts(creator, monsoon):
            print(f"  monsoon: {fact.text}")

        say("Draft -> review -> approve, across four platform variants")
        for platform in PLATFORMS:
            draft = await service.generate_draft(creator, spring, platform)
            submitted = await service.submit_for_review(creator, draft.id)
            approved = await service.approve(reviewer, submitted.id, "checked against sources")
            print(f"  {platform:<10} rev {approved.revision}  {approved.state}")
            print(f"             {approved.body[:96]}")

        say("A rejection keeps its reason, and a regeneration is a new revision")
        draft = await service.generate_draft(creator, monsoon, "linkedin")
        submitted = await service.submit_for_review(creator, draft.id)
        rejected = await service.reject(reviewer, submitted.id, "claim needs a source")
        regenerated = await service.regenerate(creator, rejected.id)
        print(f"  rejected: {rejected.state} ({rejected.reviewer_note})")
        print(f"  regenerated: revision {regenerated.revision}, state {regenerated.state}")
        for decision in await service.decision_history(regenerated.id):
            print(
                f"    {decision['action']:<9} by {decision['actor']} on rev {decision['revision']}"
            )

        say("Generated text is not authoritative memory")
        after = await service.approved_brand_facts(creator)
        print(f"  brand facts after the whole workflow: {len(after)}")
        leaked = [f.key for f in after if regenerated.body in f.text]
        print(f"  no draft text became a fact: {not leaked}")

        say("The server decides, not the client")
        for label, call in (
            ("creator approving", service.approve(creator, draft.id, "self-approval")),
            ("reviewer generating", service.generate_draft(reviewer, spring, "blog")),
            ("creator recording a brand fact", service.record_brand_fact(creator, "k", "nope")),
        ):
            try:
                await call
                print(f"  {label}: UNEXPECTEDLY ALLOWED")
                return 1
            except Denied as exc:
                print(f"  {label}: refused ({exc.reason})")

        say("Another organisation is unreachable")
        try:
            await service.campaign_facts(beta_creator, spring)
            print("  UNEXPECTED: Beta read Alpha's campaign")
            return 1
        except Denied as exc:
            print(f"  Beta reading Alpha's campaign: refused ({exc.reason})")

        await seeded.http.aclose()
        if mode != "required":
            print(
                "\nNOTE: this ran with authorization off, so Mind Palace itself "
                "answered every corpus. The refusals above are the *application's* "
                "membership checks. Re-run with MIND_PALACE_AUTH_MODE=required to "
                "exercise the memory layer's own boundary too."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
