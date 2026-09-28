#!/bin/sh
# Restore the PostgreSQL test database used by the benchmarks.
#
# The container is not durable across sessions, and a missing database produces
# partial artifacts that look like product failures. Bring it up, enable the
# extensions the archive needs, and migrate to head, or exit non-zero loudly.
set -e

NAME="${MP_PG_CONTAINER:-mp-pg-scale}"
IMAGE="docker.io/pgvector/pgvector:pg15"
URL="postgresql://mpadmin:secret@localhost:5432/mindpalace"

if venvmp/bin/python -c "
import psycopg2, sys
try:
    psycopg2.connect(host='localhost', port=5432, user='mpadmin',
                     password='secret', dbname='mindpalace', connect_timeout=3)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    echo "postgres: already up"
else
    echo "postgres: starting $NAME"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" \
        -e POSTGRES_DB=mindpalace -e POSTGRES_USER=mpadmin -e POSTGRES_PASSWORD=secret \
        -p 5432:5432 "$IMAGE" >/dev/null
    i=0
    while [ "$i" -lt 60 ]; do
        if venvmp/bin/python -c "
import psycopg2, sys
try:
    psycopg2.connect(host='localhost', port=5432, user='mpadmin',
                     password='secret', dbname='mindpalace', connect_timeout=2)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if [ "$i" -ge 60 ]; then
        echo "postgres: FAILED to become ready" >&2
        exit 1
    fi
    echo "postgres: up after ${i}s"
fi

venvmp/bin/python - <<'PY'
from sqlalchemy import create_engine, text

engine = create_engine(
    "postgresql+psycopg2://mpadmin:secret@localhost:5432/mindpalace",
    isolation_level="AUTOCOMMIT",
)
with engine.connect() as conn:
    for extension in ("vector", "pgcrypto", "pg_trgm"):
        conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
print("extensions: ready")
PY

venvmp/bin/python -m alembic -c migrations/alembic.ini \
    -x script_location=migrations upgrade head 2>&1 | tail -1
echo "migrations: at head"
