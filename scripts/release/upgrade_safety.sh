#!/usr/bin/env bash
# Upgrade safety: build a v0.9.0 archive, upgrade it to head, prove nothing moved.
#
#   DATABASE_URL=postgresql://... ./scripts/release/upgrade_safety.sh
#
# ## Why this is a CI job and not a script somebody runs
#
# Every other thing this project measures is about the code as it is now. This is
# about code that no longer exists meeting code that does, and the only machine
# that can be sure is a fresh one every time. A downgrade test that runs when
# somebody remembers has a habit of not running.
#
# ## What it does, in order
#
#   1. check out v0.9.0 into a temporary tree
#   2. migrate a FRESH database to v0.9.0's head, which is 007
#   3. populate it through v0.9.0's own `remember`, including the default corpus,
#      a superseded claim, a conflict and a snapshot
#   4. export a receipt and a Memory Pack with v0.9.0, and record them
#   5. migrate the same database to this branch's head
#   6. assert: the old receipt still verifies offline, claim identities did not
#      move, no claim was lost, and the pack is byte-identical with the clock pinned
#   7. downgrade back to 007 and up again, and assert the same things
#
# Step 3 uses the OLD source tree deliberately. An upgrade test that builds its
# fixture with the NEW code proves nothing: it reads back its own assumptions.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
OLD="$WORK/v0.9.0"
BEFORE="$WORK/before"
AFTER="$WORK/after"

export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export MIND_PALACE_RUNTIME=0
export MIND_PALACE_AUTH_MODE=off

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

say() { printf '  %s\n' "$*"; }

[ -n "${DATABASE_URL:-}" ] || { echo "::error::DATABASE_URL is required"; exit 1; }

# 1. The old release, from its tag. Not from a branch: the tag is what users
#    installed, and a branch would drift.
say "checking out v0.9.0"
git -C "$REPO" worktree add --detach --force "$OLD" v0.9.0 >/dev/null 2>&1 \
  || { echo "::error::could not check out v0.9.0"; exit 1; }

# 2. v0.9.0's schema. Its head is 007; the branch's is 010.
say "migrating the fresh database to v0.9.0's head (007)"
( cd "$OLD" && DATABASE_URL="$DATABASE_URL" PYTHONPATH="$OLD" \
    "$PY" -m alembic -c migrations/alembic.ini upgrade head >/dev/null )

# 3 + 4. Write through the OLD code, and export the proof with the OLD code.
say "populating through v0.9.0's own write path and exporting its proof"
SRC="$OLD" "$PY" "$REPO/scripts/release/make_upgrade_fixture.py" \
  --out "$BEFORE" --label v0.9.0

# 5. The upgrade, with this branch.
say "upgrading to this branch's head"
"$PY" -m alembic -c "$REPO/migrations/alembic.ini" \
  -x script_location="$REPO/migrations" upgrade head >/dev/null

# 6a. The RUNTIME-ROLE assertions.
#
#     assert_upgrade.py runs as a superuser, and a superuser bypasses row-level
#     security -- so it cannot see a policy keyed on the wrong column. This
#     project's development database carried exactly that bug for months
#     (`USING (mindpalace_allowed_corpus(id))` on `memory_documents`, whose
#     column is `corpus_id`) and every superuser test passed.
#
#     So the same archive is re-read as `mindpalace_runtime`, through a
#     credential granted to the existing corpora, and the two views must agree.
say "provisioning the least-privileged role and asserting the runtime view"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -v runtime_password=upgrade-safety-role-password \
  -f "$REPO/scripts/db/runtime_roles.sql" >/dev/null

# Same scheme, host, port and database; a different identity. The scheme is kept
# because a driver-qualified URL (`postgresql+asyncpg://`) is what the engine
# expects, and userinfo is the only part that changes.
SCHEME="${DATABASE_URL%%://*}"
REST="${DATABASE_URL##*@}"
RUNTIME_URL="${SCHEME}://mindpalace_runtime:upgrade-safety-role-password@${REST}"

if [ -z "$REST" ] || [ "$REST" = "$DATABASE_URL" ]; then
  echo "::error::could not build a runtime-role URL from DATABASE_URL"
  exit 1
fi

# The credential is issued, and the grants happen, INSIDE the assertion script.
# That is not tidiness: the operator step being asserted is "before the grant
# this reader sees one corpus, after the grant it sees them all", and the only way
# to observe the first half is to be the process that performs the second.
MIND_PALACE_SUPERUSER_URL="$DATABASE_URL" \
"$PY" "$REPO/scripts/release/assert_runtime_role.py" \
  --superuser-url "$DATABASE_URL" \
  --runtime-url "$RUNTIME_URL" \
  --before "$BEFORE"

# 6b. The archive assertions.
say "asserting the upgrade preserved the archive"
SRC="$REPO" "$PY" "$REPO/scripts/release/make_upgrade_fixture.py" \
  --out "$AFTER" --label head --export-only
"$PY" "$REPO/scripts/release/assert_upgrade.py" --before "$BEFORE" --after "$AFTER"

# 7. The round trip. A downgrade that loses memory is a release blocker; a
#    downgrade that destroys tenants is a documented consequence, not a failure,
#    so the assertions after it are about memory, not about principals.
say "downgrading to 007 and back up again"
"$PY" -m alembic -c "$REPO/migrations/alembic.ini" \
  -x script_location="$REPO/migrations" downgrade 007_claim_embedding_cache >/dev/null
"$PY" -m alembic -c "$REPO/migrations/alembic.ini" \
  -x script_location="$REPO/migrations" upgrade head >/dev/null

ROUND="$WORK/roundtrip"
SRC="$REPO" "$PY" "$REPO/scripts/release/make_upgrade_fixture.py" \
  --out "$ROUND" --label roundtrip --export-only
"$PY" "$REPO/scripts/release/assert_upgrade.py" --before "$BEFORE" --after "$ROUND" \
  --skip-runtime

git -C "$REPO" worktree remove --force "$OLD" >/dev/null 2>&1 || true
say "upgrade safety: passed"
