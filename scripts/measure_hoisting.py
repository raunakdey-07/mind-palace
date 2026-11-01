#!/usr/bin/env python3
"""Corrected measurement script for migration 016 hoisting (issue #106).

Connects as mindpalace_runtime (non-superuser) so RLS policies are actually
applied. Connects as mpadmin only for admin ops (migrations, secrets,
track_functions). Never reads job-level DATABASE_URL for measurements.

Guards (each fails the job if violated):
  - RUNTIME_DATABASE_URL for measuring connection; ADMIN_DATABASE_URL for admin;
    job-level DATABASE_URL is never read.
  - Per phase: assert current_user='mindpalace_runtime', rolsuper=false,
    rolbypassrls=false on the measuring connection.
  - 015 EXPLAIN plan must contain 'mindpalace_allowed_corpus'; 016 EXPLAIN
    plan must contain 'InitPlan' and 'mp_verified_scope_corpora'; fail
    otherwise and print the plan.

Environment:
  RUNTIME_DATABASE_URL  sync URL for probe queries (mindpalace_runtime)
  ADMIN_DATABASE_URL    sync URL for admin ops (mpadmin superuser)
  MIND_PALACE_SCOPE_SECRET  scope HMAC secret
  MIND_PALACE_ENFORCE_SCOPE_TOKEN  "on"
"""

from __future__ import annotations

import hashlib
import hmac
import os
import statistics
import subprocess
import sys
import time

import sqlalchemy as sa

_admin_raw = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")
ADMIN_DB_URL = _admin_raw.replace("postgresql://", "postgresql+psycopg2://", 1)
ADMIN_DB_URL_PLAIN = _admin_raw  # for psql subprocess calls
_runtime_raw = os.environ.get("RUNTIME_DATABASE_URL", "postgresql://mindpalace_runtime:ci-runtime-role-password@localhost:5432/mindpalace")
RUNTIME_DB_URL = _runtime_raw.replace("postgresql://", "postgresql+psycopg2://", 1)
SCOPE_SECRET = os.environ.get("MIND_PALACE_SCOPE_SECRET", "dev-scope-secret")
ENFORCE = os.environ.get("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off")

N_CLAIMS = 50000
N_REPEATS = 5


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        print(f"::error::Command failed (rc={result.returncode}): {' '.join(cmd)}")
        sys.exit(result.returncode)
    return result


def alembic_upgrade(revision: str) -> None:
    run(
        [
            "python",
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "-x",
            "script_location=migrations",
            "upgrade",
            revision,
        ],
        env={**os.environ, "DATABASE_URL": ADMIN_DB_URL},
    )


def enable_track_functions() -> None:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER DATABASE mindpalace SET track_functions = 'all'"))
    engine.dispose()


def enable_enforcement(secret: str, enabled: bool) -> None:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO memory_server_secrets (name, value) "
                "VALUES ('scope_hmac_secret', :s) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"s": secret},
        )
        conn.execute(
            sa.text("SELECT mp_set_scope_token_enforcement(:e)"),
            {"e": "on" if enabled else "off"},
        )
    engine.dispose()


def apply_runtime_roles() -> None:
    run(
        [
            "psql",
            ADMIN_DB_URL_PLAIN,
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "runtime_password=ci-runtime-role-password",
            "-f",
            "scripts/db/runtime_roles.sql",
        ],
    )


def reset_counters() -> None:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.begin() as conn:
        for fn in (
            "mp_verified_scope_corpora()",
            "mp_verify_scope_token(CHAR(64),TEXT)",
            "mindpalace_allowed_corpus(CHAR(64))",
        ):
            conn.execute(
                sa.text(
                    f"SELECT pg_stat_reset_single_function_counters('{fn}'::regprocedure)"
                )
            )
    engine.dispose()


def read_counters() -> dict:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT funcname, calls FROM pg_stat_user_functions "
                "WHERE funcname IN ('mp_verified_scope_corpora', "
                "'mp_verify_scope_token', 'mindpalace_allowed_corpus')"
            )
        ).fetchall()
    engine.dispose()
    return {r[0]: r[1] for r in rows}


def check_head() -> str:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT version_num FROM alembic_version")).fetchone()
    engine.dispose()
    print(f"alembic_version={row[0]}")
    return row[0]


def check_policies() -> list:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT policyname, qual FROM pg_policies "
                "WHERE tablename = 'memory_claims'"
            )
        ).fetchall()
    engine.dispose()
    for name, qual in rows:
        print(f"  policy={name} qual={qual}")
    return [(name, qual) for name, qual in rows]


def assert_policy_contains(phase: str, expected_substring: str) -> None:
    engine = sa.create_engine(ADMIN_DB_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT policyname, qual FROM pg_policies "
                "WHERE tablename = 'memory_claims'"
            )
        ).fetchall()
    engine.dispose()
    found = any(expected_substring in (qual or "") for _, qual in rows)
    if not found:
        print(f"::error::Phase {phase}: no policy qual contains '{expected_substring}'")
        sys.exit(1)
    print(f"  OK: phase {phase} policy qual contains '{expected_substring}'")


def assert_runtime_role(phase: str, engine: sa.Engine) -> None:
    """Assert the measuring connection is mindpalace_runtime, not superuser."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT current_user, session_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).fetchone()
    print(f"  {phase}: current_user={row[0]} session_user={row[1]} rolsuper={row[2]} rolbypassrls={row[3]}")
    if row[0] != "mindpalace_runtime":
        print(f"::error::{phase}: expected current_user=mindpalace_runtime, got {row[0]}")
        sys.exit(1)
    if row[2]:
        print(f"::error::{phase}: rolsuper=true (superuser bypasses RLS)")
        sys.exit(1)
    if row[3]:
        print(f"::error::{phase}: rolbypassrls=true (bypasses RLS)")
        sys.exit(1)


def seed_claims(engine: sa.Engine, n: int = N_CLAIMS) -> list[str]:
    corpus_names = ["hoist-alpha", "hoist-beta", "hoist-gamma"]
    cids = [hashlib.sha256(f"corpus:{name}".encode()).hexdigest() for name in corpus_names]

    with engine.begin() as conn:
        for name, cid in zip(corpus_names, cids):
            conn.execute(
                sa.text(
                    "INSERT INTO corpora (id, name) VALUES (:c, :n) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"c": cid, "n": name},
            )
            path = f"/{name}/doc.md"
            doc_id = hashlib.sha256((cid + ":" + path).encode()).hexdigest()
            ver_id = hashlib.sha256(f"ver:{cid}".encode()).hexdigest()
            chunk_id = hashlib.sha256(f"chunk:{cid}".encode()).hexdigest()
            conn.execute(
                sa.text(
                    "INSERT INTO memory_documents (corpus_id, id, path) "
                    "VALUES (:c, :d, :p) ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "d": doc_id, "p": path},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO memory_versions "
                    "(corpus_id, memory_document_id, id, version_number, event, observed_at, "
                    "content, metadata, fingerprint, document_id) "
                    "VALUES (:c, :d, :v, 1, 'NEW', now(), 'c', '{}'::jsonb, 'fp', :d) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "d": doc_id, "v": ver_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO memory_chunks (corpus_id, version_id, id, text, order_index) "
                    "VALUES (:c, :v, :k, 'evidence text for validation', 0) ON CONFLICT DO NOTHING"
                ),
                {"c": cid, "v": ver_id, "k": chunk_id},
            )
            per = n // len(corpus_names)
            remainder = n - per * len(corpus_names)
            if name == corpus_names[-1]:
                per += remainder
            for j in range(per):
                kid = hashlib.sha256(f"{cid}:{j}".encode()).hexdigest()
                eid = hashlib.sha256(f"e:{cid}:{j}".encode()).hexdigest()
                conn.execute(
                    sa.text(
                        "INSERT INTO memory_claims "
                        "(corpus_id, memory_document_id, version_id, id, key, claim, value, "
                        "info_state, extraction_mode, provenance_state) "
                        "VALUES (:c, :d, :v, :kid, :key, :claim, '{}'::jsonb, "
                        "'approved', 'deterministic', 'direct_claim') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"c": cid, "d": doc_id, "v": ver_id, "kid": kid, "key": f"k-{j}", "claim": f"hoist claim {j}"},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO memory_evidence "
                        "(corpus_id, version_id, id, claim_id, chunk_id, quote, start_offset, end_offset) "
                        "VALUES (:c, :v, :eid, :kid, :chunk, 'evidence', 0, 8) ON CONFLICT DO NOTHING"
                    ),
                    {"c": cid, "v": ver_id, "eid": eid, "kid": kid, "chunk": chunk_id},
                )

    print(f"Seeded {n} claims across {len(corpus_names)} corpora")
    return cids


def run_explain(
    label: str,
    scope: str,
    secret: str,
    enforce: bool,
    rt_engine: sa.Engine,
    phase: str,
) -> tuple[float, str]:
    """Run EXPLAIN ANALYZE as mindpalace_runtime. Returns (wall_time_ms, plan_text)."""
    with rt_engine.begin() as conn:
        conn.execute(
            sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
            {"s": scope},
        )
        token = hmac.new(secret.encode(), scope.encode(), hashlib.sha256).hexdigest()
        conn.execute(
            sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
            {"t": token},
        )
        t0 = time.perf_counter()
        result = conn.execute(
            sa.text("EXPLAIN (ANALYZE, VERBOSE, BUFFERS) SELECT count(*) FROM memory_claims")
        )
        plan = "\n".join(str(r[0]) for r in result.fetchall())
        t1 = time.perf_counter()
        wall_ms = (t1 - t0) * 1000
        print(f"--- {label}: FULL CORPUS EXPLAIN ---")
        print(plan)
        print(f"wall_time_ms={wall_ms:.2f}")

        # Assert policy was applied in the plan
        expected = "mindpalace_allowed_corpus" if phase == "015" else "InitPlan"
        if expected not in plan:
            print(f"::error::{label}: expected '{expected}' in EXPLAIN plan")
            sys.exit(1)
        print(f"  OK: {label} plan contains '{expected}'")

        if phase == "016":
            if "mp_verified_scope_corpora" not in plan:
                print(f"::error::{label}: expected 'mp_verified_scope_corpora' in 016 plan")
                sys.exit(1)
            print(f"  OK: {label} plan contains 'mp_verified_scope_corpora'")

        return wall_ms, plan


def run_explain_narrow(
    label: str,
    scope: str,
    secret: str,
    enforce: bool,
    rt_engine: sa.Engine,
    phase: str,
    first_cid: str,
) -> tuple[float, str]:
    """Run EXPLAIN ANALYZE narrow indexed query as mindpalace_runtime."""
    with rt_engine.begin() as conn:
        conn.execute(
            sa.text("SELECT set_config('mindpalace.tenant_scope', :s, true)"),
            {"s": scope},
        )
        token = hmac.new(secret.encode(), scope.encode(), hashlib.sha256).hexdigest()
        conn.execute(
            sa.text("SELECT set_config('mindpalace.scope_token', :t, true)"),
            {"t": token},
        )
        first_cid = scope.split(",")[0]
        t0 = time.perf_counter()
        result = conn.execute(
            sa.text("EXPLAIN (ANALYZE, VERBOSE, BUFFERS) SELECT count(*) FROM memory_claims WHERE corpus_id = :c"),
            {"c": first_cid},
        )
        plan = "\n".join(str(r[0]) for r in result.fetchall())
        t1 = time.perf_counter()
        wall_ms = (t1 - t0) * 1000
        print(f"--- {label}: NARROW INDEXED QUERY EXPLAIN ---")
        print(plan)
        print(f"wall_time_ms={wall_ms:.2f}")
        return wall_ms, plan


def extract_actual_rows(plan_text: str) -> int:
    import re
    matches = re.findall(r"rows=(\d+)", plan_text)
    if matches:
        return int(matches[-1])
    return -1


def extract_plan_shape(plan_text: str) -> str:
    lines = []
    for line in plan_text.split("\n"):
        stripped = line.strip()
        if any(kw in stripped for kw in ("InitPlan", "SubPlan", "Filter", "HashFilter")):
            lines.append(stripped)
    return "\n".join(lines)


def measure_condition(
    label: str,
    phase: str,
    scope: str,
    secret: str,
    enforce: bool,
    rt_engine: sa.Engine,
    first_cid: str,
) -> None:
    print(f"\n=== {label} ===")

    # Set enforcement flag via admin connection (direct insert, bypass function permission)
    admin_engine = sa.create_engine(ADMIN_DB_URL)
    with admin_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO memory_server_secrets (name, value) "
                "VALUES ('enforce_scope_token', :e) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"e": "on" if enforce else "off"},
        )
    admin_engine.dispose()

    # Assert runtime role before measuring
    assert_runtime_role(phase, rt_engine)

    # Reset counters before
    before = read_counters()

    # Warm-up run
    run_explain(f"{label}-warmup-full", scope, secret, enforce, rt_engine, phase)
    run_explain_narrow(f"{label}-warmup-narrow", scope, secret, enforce, rt_engine, phase, first_cid)

    # 5 measured runs
    full_times = []
    narrow_times = []
    full_plans = []
    narrow_plans = []
    full_rows = []
    narrow_rows = []

    for i in range(N_REPEATS):
        t, p = run_explain(f"{label}-run{i+1}-full", scope, secret, enforce, rt_engine, phase)
        full_times.append(t)
        full_plans.append(p)
        full_rows.append(extract_actual_rows(p))

        t, p = run_explain_narrow(f"{label}-run{i+1}-narrow", scope, secret, enforce, rt_engine, phase, first_cid)
        narrow_times.append(t)
        narrow_plans.append(p)
        narrow_rows.append(extract_actual_rows(p))

    print(f"\n=== {label}: ALL FULL CORPUS RUNS ===")
    for i, t in enumerate(full_times):
        print(f"  run {i+1}: wall_time_ms={t:.2f}")
        print(f"    plan_shape:\n{extract_plan_shape(full_plans[i])}")
    print(f"  median wall_time_ms={statistics.median(full_times):.2f}")

    print(f"\n=== {label}: ALL NARROW INDEXED QUERY RUNS ===")
    for i, t in enumerate(narrow_times):
        print(f"  run {i+1}: wall_time_ms={t:.2f}")
        print(f"    plan_shape:\n{extract_plan_shape(narrow_plans[i])}")
    print(f"  median wall_time_ms={statistics.median(narrow_times):.2f}")

    # Assert row counts
    expected_full = N_CLAIMS
    expected_narrow = N_CLAIMS // 3  # 50000 / 3 = 16666
    if any(r != expected_full for r in full_rows):
        print(f"::error::Full corpus actual rows {full_rows} != expected {expected_full}")
        sys.exit(1)
    if any(r != expected_narrow for r in narrow_rows):
        print(f"::error::Narrow query actual rows {narrow_rows} != expected {expected_narrow}")
        sys.exit(1)
    print(f"  OK: full corpus rows={full_rows[0]} (expected {expected_full})")
    print(f"  OK: narrow query rows={narrow_rows[0]} (expected {expected_narrow})")

    # Read counters after
    after = read_counters()
    delta = {}
    for fn in ("mp_verified_scope_corpora", "mp_verify_scope_token", "mindpalace_allowed_corpus"):
        delta[fn] = after.get(fn, 0) - before.get(fn, 0)
    print(f"\n=== {label}: FUNCTION CALL COUNTS (delta) ===")
    for fn in ("mp_verified_scope_corpora", "mp_verify_scope_token", "mindpalace_allowed_corpus"):
        print(f"  {fn}: {delta[fn]}")


def main() -> None:
    print(f"SCOPE_SECRET set={bool(SCOPE_SECRET)}")
    print(f"ENFORCE={ENFORCE}")

    # Guard: never read job-level DATABASE_URL for measurements
    if "DATABASE_URL" in os.environ:
        print("  WARNING: job-level DATABASE_URL set but not used by this script")

    rt_engine = sa.create_engine(RUNTIME_DB_URL)
    admin_engine = sa.create_engine(ADMIN_DB_URL)

    # Step 1: Enable track_functions (new sessions)
    print("\n=== Enabling track_functions ===")
    enable_track_functions()

    # Step 2: Upgrade to 015 only
    print("\n=== Upgrading to 015_rls_scope_enforcement ===")
    alembic_upgrade("015_rls_scope_enforcement")
    head = check_head()
    assert head == "015_rls_scope_enforcement", f"Expected 015, got {head}"

    print("\n=== Policies at 015 ===")
    policies = check_policies()
    assert_policy_contains("015", "mindpalace_allowed_corpus")

    # Step 3: Run runtime_roles.sql
    print("\n=== Running runtime_roles.sql ===")
    apply_runtime_roles()

    # Step 4: Rotate secret and enable enforcement
    print("\n=== Rotating secret and enabling enforcement ===")
    enable_enforcement(SCOPE_SECRET, ENFORCE == "on")

    # Step 5: Seed claims
    print("\n=== Seeding claims ===")
    cids = seed_claims(admin_engine, N_CLAIMS)
    scope = ",".join(cids)
    print(f"Scope corpus_ids: {scope}")

    # Step 6: Measure at 015
    print("\n\n##### MEASUREMENT AT 015 #####")
    measure_condition("015-enforcement-off", "015", scope, SCOPE_SECRET, False, rt_engine, scope)
    measure_condition("015-enforcement-on", "015", scope, SCOPE_SECRET, True, rt_engine, scope)

    # Step 7: Upgrade to 016
    print("\n\n##### Upgrading to 016_hoist_scope_initplan #####")
    alembic_upgrade("016_hoist_scope_initplan")
    head = check_head()
    assert head == "016_hoist_scope_initplan", f"Expected 016, got {head}"

    print("\n=== Policies at 016 ===")
    policies = check_policies()
    assert_policy_contains("016", "mp_verified_scope_corpora()")

    # Step 8: Re-apply runtime grants if needed
    print("\n=== Re-applying runtime_roles.sql ===")
    apply_runtime_roles()

    # Step 9: Measure at 016
    print("\n\n##### MEASUREMENT AT 016 #####")
    measure_condition("016-enforcement-off", "016", scope, SCOPE_SECRET, False, rt_engine, scope)
    measure_condition("016-enforcement-on", "016", scope, SCOPE_SECRET, True, rt_engine, scope)

    print("\n\n##### MEASUREMENT COMPLETE #####")


if __name__ == "__main__":
    main()
