# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The one place that explains a dead database.

Both `mindpalace init` and the per-command failure text need to say the same
thing, so the guidance lives here and is imported rather than restated. The
underlying exception is never the only thing a developer sees, and never the
first: it is appended for debugging, after the reason and the command to run.
"""

from __future__ import annotations


def database_hint() -> str:
    """Where Mind Palace believes the database is, named for this process.

    Read from the environment rather than hardcoded, so the advice names the
    database this command would actually use. Never raises: advice that fails is
    worse than no advice.
    """
    url = _url()
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        return f"{parsed.host or 'localhost'}:{parsed.port or 5432}"
    except Exception:  # noqa: BLE001 - a malformed URL must not break the message
        return url


def _url() -> str:
    import os

    return os.getenv("DATABASE_URL", "postgresql://mpadmin:secret@localhost:5432/mindpalace")


def storage_unavailable(exc: Exception | None = None) -> str:
    """The full explanation of a database that cannot be reached.

    WHAT happened, WHY, and the exact commands to run.
    """
    detail = f"\n\n{type(exc).__name__}: {exc}" if exc is not None else ""
    return (
        "Mind Palace needs persistent storage, and it could not reach it.\n"
        "\n"
        f"Why: it could not connect to PostgreSQL at {database_hint()}.\n"
        "\n"
        "What to do:\n"
        "  docker compose up -d postgresql\n"
        "\n"
        "Then check it works:\n"
        "  python -m alembic -c migrations/alembic.ini upgrade head\n"
        "  mindpalace init" + detail
    )
