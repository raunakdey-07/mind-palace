"""Build a v0.9.0-era archive through the real write path, then export proof.

    python scripts/release/make_upgrade_fixture.py --out /tmp/fixture

Run it with the SOURCE TREE OF THE VERSION UNDER TEST on sys.path -- v0.9.0 to
make the old fixture, HEAD to re-export it after an upgrade.

## Why this exists

An upgrade test that builds its fixture with the NEW code proves nothing. If the
old release could not write the row, or wrote it differently, the new release
reads back its own assumptions and calls the migration safe.

So the fixture is written by v0.9.0, through `api.services.remember` -- the same
call the CLI makes -- and everything afterwards is comparison.

## What goes in, and why each thing

| what | why it is the hard case |
|---|---|
| the default corpus | its id is 63 chars in a `char(64)` column; every other corpus id is 64. |
| a superseded claim | `remember` twice on one key; the first stays as history. |
| a conflict | two keys, one subject, two values. Conflicts are computed, not stored. |
| a snapshot | a sealed, immutable pack. A migration that rewrites claim rows breaks the seal. |
| a second corpus | proves scoping survives, not just the default. |

## What comes out

* `receipt.json` -- the v0.9.0 receipt, verifiable with no database and no model
* `pack.json`    -- the authoritative Memory Pack at v0.9.0
* `claims.json`  -- claim identities, which must not move across an upgrade
* `state.json`   -- alembic version, corpus list, claim count
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default="fixture")
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="read the archive and emit pack/receipt/claims WITHOUT writing. "
        "Required for the after-upgrade export: re-running the writes would "
        "double every claim and the assertion would be measuring the harness.",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, os.environ["SRC"])
    from sqlalchemy import text

    from api.services import db as dbm

    # v0.9.0 predates the principal model: it has neither `api.services.authz`
    # nor `db.principal_scope` / `db.admin_session_scope`. The fixture has to run
    # on both sides of that change, because the whole point is to build the old
    # archive with the old code.
    try:
        from api.services import authz

        ADMIN = authz.ADMIN_PRINCIPAL
    except ImportError:
        ADMIN = None

    def admin():
        return dbm.principal_scope(ADMIN) if ADMIN is not None else contextlib.nullcontext()

    scope = dbm.admin_session_scope if hasattr(dbm, "admin_session_scope") else dbm.session_scope
    from api.services.corpora import get_or_create_corpus
    from api.services.remember import remember

    async def build():
        async with scope() as db:
            with admin():
                # The default corpus already exists (migration 002). Creating it
                # again would raise, so only the second one is created here.
                for name in ("upgrade-fixture",):
                    await get_or_create_corpus(db, name)
            await db.commit()

        written = []
        # -- the default corpus, and a superseded claim on one key -----------
        written.append(
            await remember(
                "The production datastore is PostgreSQL.", corpus="default", key="infra.datastore"
            )
        )
        written.append(
            await remember(
                "The incident response SLA is 30 minutes.", corpus="default", key="ops.sla"
            )
        )
        written.append(
            await remember(
                "The production datastore is distributed CockroachDB.",
                corpus="default",
                key="infra.datastore",
            )
        )  # supersedes the first
        # -- a conflict: two keys, one subject, different values ------------
        written.append(
            await remember(
                "The rate limit is 1000 requests per second.",
                corpus="default",
                key="limits.rate.primary",
            )
        )
        written.append(
            await remember(
                "The rate limit is 5000 requests per second.",
                corpus="default",
                key="limits.rate.secondary",
            )
        )
        # -- a second corpus, so scoping is not just the default -------------
        written.append(
            await remember(
                "The support team answers within one business day.",
                corpus="upgrade-fixture",
                key="support.sla",
            )
        )

        async with scope() as db:
            with admin():
                from api.services.memory import snapshot as take_snapshot

                # NOT corpus_id_for_name("default"): the default corpus has its
                # own literal id from migration 002, 63 characters long, while
                # corpus_id_for_name hashes the NAME. Using the hash points at a
                # corpus that does not exist, and the fixture fails on a detail
                # that has nothing to do with upgrades.
                from api.services.corpora import DEFAULT_CORPUS_ID

                snap = await take_snapshot(db, corpus_id=DEFAULT_CORPUS_ID)
                await db.commit()
        return written, snap

    if args.export_only:
        # Read-only. The after-upgrade export must not write: re-running the
        # writes would double every claim, and the assertion that compares
        # before with after would be measuring the harness rather than the
        # migration.
        writes, snap = [], None
    else:
        writes, snap = asyncio.run(build())

    from mindpalace_sdk import MindPalace

    mp = MindPalace(name="default")
    question = "What datastore does production use?"

    # Two artifacts, built the way the CLI builds them.
    #
    # The PACK is `memory.pack()`: the authoritative response, with no proof.
    # The RECEIPT is `memory.query(..., include_receipt=True)` wrapped in the
    # same bundle `mindpalace receipt` writes -- response plus proof, so it can
    # be verified offline with no database and no model.
    #
    # Asking pack() for a receipt and calling that a receipt does not work: the
    # proof describes the response, and the pack endpoint returns a different one.
    pack_response = mp.memory.pack(query=question)
    (out / "pack.json").write_text(pack_response.model_dump_json())

    query_response = mp.memory.query(question, include_receipt=True)
    if not query_response.receipt:
        raise SystemExit(
            "make_upgrade_fixture: the question produced no answer, so there is "
            "nothing to prove. A receipt records what was actually returned."
        )
    (out / "receipt.json").write_text(
        json.dumps(
            {
                "response": query_response.model_dump(mode="json"),
                "receipt": query_response.receipt,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )

    # Claim identities: the `memory_document_id` of every versioned document.
    async def identities():
        async with scope() as db:
            with admin():
                version = (
                    await db.execute(text("SELECT version_num FROM alembic_version"))
                ).first()
                rows = (
                    await db.execute(
                        text(
                            "SELECT d.path, d.id, d.corpus_id, count(v.id) FROM memory_documents d "
                            "LEFT JOIN memory_versions v ON v.memory_document_id = d.id "
                            "GROUP BY d.path, d.id, d.corpus_id ORDER BY d.path"
                        )
                    )
                ).all()
                corpora = (
                    (await db.execute(text("SELECT name FROM corpora ORDER BY name")))
                    .scalars()
                    .all()
                )
                claims = (await db.execute(text("SELECT count(*) FROM memory_claims"))).scalar()
        return {
            "alembic_version": version[0],
            "corpora": list(corpora),
            "claim_count": claims,
            "documents": [
                {"path": r[0], "id": r[1], "corpus_id": r[2], "versions": r[3]} for r in rows
            ],
        }

    state = asyncio.run(identities())
    (out / "claims.json").write_text(json.dumps(state, indent=2, sort_keys=True))

    print(
        f"{args.label}: alembic={state['alembic_version']} "
        f"corpora={len(state['corpora'])} claims={state['claim_count']} "
        f"documents={len(state['documents'])} writes={len(writes)}"
    )
    if snap:
        print(f"{args.label}: snapshot taken")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
