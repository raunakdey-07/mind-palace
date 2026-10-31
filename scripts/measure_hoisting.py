#!/usr/bin/env python3
"""Measurement script for migration 016 hoisting.

Run in CI with Postgres available. Seeds >=50,000 memory_claims, runs as
mindpalace_runtime, enforcement off and on, at alembic upgrade 015 (before)
and 016 (after). Prints EXPLAIN (ANALYZE, VERBOSE, BUFFERS) for full-corpus
and narrow indexed queries, plus pg_stat_user_functions call counts and wall
times. Raw output only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import time

import sqlalchemy as sa


def sync_url():
    url = os.environ.get("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")
    return (
        url.replace("postgresql+psycopg://", "postgresql+psycopg2://")
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def runtime_url_sync():
    base = sync_url()
    return base.replace("mpadmin:secret", "mindpalace_runtime:ci-runtime-role-password")


def apply_migrations(revision):
    import subprocess
    result = subprocess.run(
        ["python", "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", revision],
        capture_output=True, text=True,
    )
    print(f"alembic upgrade {revision}: rc={result.returncode}")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        sys.exit(1)


def seed_claims(n=50000):
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            # Create 3 corpora
            corpus_names = ["hoist-alpha", "hoist-beta", "hoist-gamma"]
            cids = [hashlib.sha256(f"corpus:{n}".encode()).hexdigest() for n in corpus_names]
            for name, cid in zip(corpus_names, cids):
                conn.execute(
                    sa.text("INSERT INTO corpora (id, name) VALUES (:c, :n) ON CONFLICT (id) DO NOTHING"),
                    {"c": cid, "n": name},
                )
                path = f"/{name}/doc.md"
                doc_id = hashlib.sha256((cid + ":" + path).encode()).hexdigest()
                ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
                chunk_id = hashlib.sha256(f"chunk:{cid}".encode()).hexdigest()
                conn.execute(
                    sa.text("INSERT INTO memory_documents (corpus_id, id, path) VALUES (:c, :d, :p) ON CONFLICT DO NOTHING"),
                    {"c": cid, "d": doc_id, "p": path},
                )
                conn.execute(
                    sa.text("INSERT INTO memory_versions (corpus_id, memory_document_id, id, version_number, event, observed_at, content, metadata, fingerprint, document_id) VALUES (:c, :d, :v, 1, 'NEW', now(), 'c', '{}'::jsonb, 'fp', :d) ON CONFLICT DO NOTHING"),
                    {"c": cid, "d": doc_id, "v": ver_id},
                )
                conn.execute(
                    sa.text("INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index) VALUES (:c, :v, :k, 'evidence text', 0) ON CONFLICT DO NOTHING"),
                    {"c": cid, "v": ver_id, "k": chunk_id},
                )
                per = n // len(corpus_names)
                for j in range(per):
                    kid = hashlib.sha256(f"{cid}:{j}".encode()).hexdigest()
                    eid = hashlib.sha256(f"e:{cid}:{j}".encode()).hexdigest()
                    conn.execute(
                        sa.text("INSERT INTO memory_claims (corpus_id, memory_document_id, version_id, id, key, claim, value, info_state, extraction_mode, provenance_state) VALUES (:c, :d, :v, :kid, :key, :claim, '{}'::jsonb, 'approved', 'deterministic', 'direct_claim') ON CONFLICT DO NOTHING"),
                        {"c": cid, "d": doc_id, "v": ver_id, "kid": kid, "key": f"k-{j}", "claim": f"hoist claim {j}"},
                    )
                    conn.execute(
                        sa.text("INSERT INTO memory_evidence (corpus_id, version_id, id, claim_id, chunk_id, quote, start_offset, end_offset) VALUES (:c, :v, :eid, :kid, :chunk, 'evidence', 0, 8) ON CONFLICT DO NOTHING"),
                        {"c": cid, "v": ver_id, "eid": eid, "kid": kid, "chunk": chunk_id},
                    )
            print(f"Seeded {n} claims across {len(corpus_names)} corpora")
    finally:
        engine.dispose()


def run_explain_and_counters(label, scope, secret, enforce):
    rt_engine = sa.create_engine(runtime_url_sync(), poolclass=sa.pool.NullPool)
    try:
        with rt_engine.begin() as conn:
            conn.execute(sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"), {"s": scope})
            token = hmac.new(secret.encode(), scope.encode(), hashlib.sha256).hexdigest()
            conn.execute(sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"), {"t": token})
            if not enforce:
                conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(false)"))
            else:
                conn.execute(sa.text("SELECT mp_set_scope_token_enforcement(true)"))

            # Full corpus
            t0 = time.time()
            result = conn.execute(sa.text("EXPLAIN (ANALYZE, VERBOSE, BUFFERS) SELECT count(*) FROM memory_claims"))
            plan = "\n".join(str(r[0]) for r in result.fetchall())
            t1 = time.time()
            print(f"\n=== {label}: FULL CORPUS EXPLAIN ===")
            print(plan)
            print(f"wall_time_s={t1-t0:.4f}")

            # Narrow indexed query
            t0 = time.time()
            result = conn.execute(sa.text("EXPLAIN (ANALYZE, VERBOSE, BUFFERS) SELECT count(*) FROM memory_claims WHERE corpus_id = :c"), {"c": scope.split(",")[0]})
            plan2 = "\n".join(str(r[0]) for r in result.fetchall())
            t1 = time.time()
            print(f"\n=== {label}: NARROW INDEXED QUERY EXPLAIN ===")
            print(plan2)
            print(f"wall_time_s={t1-t0:.4f}")
    finally:
        rt_engine.dispose()

    # Read counters as superuser
    read_engine = sa.create_engine(sync_url())
    try:
        with read_engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT funcname, calls FROM pg_stat_user_functions "
                "WHERE funcname IN ('mp_verified_scope_corpora', 'mp_verify_scope_token', 'mindpalace_allowed_corpus')"
            )).fetchall()
            print(f"\n=== {label}: FUNCTION CALL COUNTS ===")
            for r in rows:
                print(f"  {r[0]}: {r[1]}")
    finally:
        read_engine.dispose()


def main():
    secret = os.environ.get("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")
    corpus_names = ["hoist-alpha", "hoist-beta", "hoist-gamma"]
    cids = [hashlib.sha256(f"corpus:{n}".encode()).hexdigest() for n in corpus_names]
    scope = ",".join(cids)

    # Reset function counters
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            for fn in ("mp_verified_scope_corpora()", "mp_verify_scope_token(CHAR(64),TEXT)", "mindpalace_allowed_corpus(CHAR(64))"):
                conn.execute(sa.text(f"SELECT pg_stat_reset_single_function_counters('{fn}'::regprocedure)"))
    finally:
        engine.dispose()

    # Phase 1: at 015 (before hoisting)
    print("\n\n##### PHASE 1: alembic upgrade 015 (before hoisting) #####")
    apply_migrations("015_rls_scope_enforcement")
    seed_claims(50000)
    run_explain_and_counters("015-enforcement-off", scope, secret, enforce=False)
    run_explain_and_counters("015-enforcement-on", scope, secret, enforce=True)

    # Reset counters
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            for fn in ("mp_verified_scope_corpora()", "mp_verify_scope_token(CHAR(64),TEXT)", "mindpalace_allowed_corpus(CHAR(64))"):
                conn.execute(sa.text(f"SELECT pg_stat_reset_single_function_counters('{fn}'::regprocedure)"))
    finally:
        engine.dispose()

    # Phase 2: upgrade to 016 (after hoisting)
    print("\n\n##### PHASE 2: alembic upgrade 016 (after hoisting) #####")
    apply_migrations("016_hoist_scope_initplan")
    run_explain_and_counters("016-enforcement-off", scope, secret, enforce=False)
    run_explain_and_counters("016-enforcement-on", scope, secret, enforce=True)

    print("\n\n##### MEASUREMENT COMPLETE #####")


if __name__ == "__main__":
    main()
