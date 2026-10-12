"""What a failure is allowed to say, and where.

A memory system stores things people would not put in a log file: a diagnosis, a
dispute, a salary, a name. Two mechanisms were quietly copying that content into
operator-visible places, and neither was obvious from reading the code that
wrote it.

**PostgreSQL puts the offending row in the error detail.** Every constraint
violation comes back as

    DETAIL:  Failing row contains (..., "my partner and I are trying again", ...)

and asyncpg passes that string through verbatim. SQLAlchemy's
`hide_parameters` does not help -- that setting suppresses *bound parameters* in
the rendered SQL, and this text arrives from the server, not from the statement.
Any traceback that prints the exception therefore writes memory content to the
log, and a log aggregator keeps it long after the request that produced it.

**Three cache-failure log sites printed the same traceback.** They exist to keep
an L2 embedding cache from breaking a read or a write, and they were all
`logger.warning(..., exc_info=True)` on a statement whose arguments were claim
text.

Both are fixed by the sanitizing 500 handler in `api/main.py` and by those three
sites logging the exception's type instead of its body. These tests are the proof,
and each one fails if the fix is reverted.
"""

from __future__ import annotations

import io
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tenant_harness import RUNTIME_ROLE

#: A string that must never appear in a response body, a log line, or readiness
#: output. Distinctive enough to grep for, and shaped like the thing it stands in
#: for -- something a customer would mind being copied out of context.
CANARY = "CANARY_PRIVATE_FACT_partner_and_i_are_trying_again"

#: A route that exists only for the duration of one test.
PROBE_PATH = "/__hygiene_probe__"


def _capture_api_logs() -> tuple[logging.Handler, io.StringIO]:
    """Collect what the application logger writes while the block runs."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("mindpalace.api")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return handler, stream


@pytest.fixture
def api_logs():
    handler, stream = _capture_api_logs()
    try:
        yield stream
    finally:
        logging.getLogger("mindpalace.api").removeHandler(handler)


def _mount_failing_route(app, exc: Exception):
    """Add a route that raises `exc`, and return a function that removes it.

    A real request through the real middleware stack, because the thing under
    test is whether the *installed* middleware catches it -- not whether a
    function called in isolation returns a tidy string.
    """
    from fastapi import APIRouter

    router = APIRouter()

    @router.get(PROBE_PATH)
    async def _probe():  # pragma: no cover - exercised through the client
        raise exc

    app.include_router(router)

    def remove():
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != PROBE_PATH
        ]

    return remove


@pytest.fixture
def unauthenticated_probe(monkeypatch):
    """Let the probe route through the authentication middleware.

    The suite runs in both modes, and CI runs it with
    `MIND_PALACE_AUTH_MODE=required`. Without this the probe is answered `401`
    before it ever reaches the middleware under test -- and the test would pass
    for the wrong reason, having asserted a 500 it never got.

    Scoped to the test rather than added to the production prefix list, so no
    unprefixed route is left unauthenticated even for the duration of one test.
    """
    import api.main as main_module

    monkeypatch.setattr(
        main_module, "_UNAUTHENTICATED_PREFIXES", main_module._UNAUTHENTICATED_PREFIXES + ("/__",)
    )


async def _constraint_error() -> Exception:
    """An exception whose text genuinely carries a row, as PostgreSQL sends it.

    Built by asking the database to fail, rather than by writing the string by
    hand. A hand-written DETAIL would be a test of the test.
    """
    from sqlalchemy.exc import IntegrityError

    from api.services import authz
    from api.services.db import principal_scope, session_scope

    async with session_scope() as db:
        with principal_scope(authz.LOCAL_PRINCIPAL):
            with pytest.raises(IntegrityError) as caught:
                await db.execute(
                    text("INSERT INTO memory_claims (id, corpus_id, claim) " "VALUES (:i, :c, :s)"),
                    {"i": "hygiene-probe", "c": "hygiene-probe", "s": CANARY},
                )
        await db.rollback()
    return caught.value


async def test_a_postgresql_constraint_detail_really_does_carry_the_row():
    """The premise, checked rather than assumed.

    If a future driver or PostgreSQL version stopped including the row, the
    sanitizing handler would become decorative and the tests below would be
    asserting nothing. This one fails if that happens, so the rest can be read as
    evidence rather than as caution.
    """
    exc = await _constraint_error()
    assert CANARY in str(exc), (
        "the database no longer includes the offending row in the error detail, "
        "so this suite's premise no longer holds; re-check whether the "
        "sanitizing handler is still needed before removing these tests"
    )


async def test_an_unhandled_database_error_returns_no_memory_and_no_detail(
    api_logs, unauthenticated_probe
):
    """The response body an integrator sees must carry nothing from the row."""
    from api.main import app

    exc = await _constraint_error()
    remove = _mount_failing_route(app, exc)
    try:
        # `raise_app_exceptions` left at its default, which re-raises whatever
        # escapes the app. A registered handler means nothing escapes -- and that
        # is the assertion doing the real work here: an exception that propagates
        # is what uvicorn prints as "Exception in ASGI application", traceback
        # and all, which is the leak this file exists to close.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get(PROBE_PATH)
    finally:
        remove()

    assert response.status_code == 500
    body = response.text
    assert CANARY not in body, "the response echoed the memory body"
    # The PostgreSQL internals are equally unwelcome: a stack of SQLAlchemy and
    # asyncpg frames tells an integrator nothing they can act on and tells a
    # scanner exactly what this service runs.
    assert "asyncpg" not in body
    assert "sqlalchemy" not in body.lower()


async def test_an_unhandled_database_error_logs_no_memory(api_logs, unauthenticated_probe):
    """The half that matters more: the log outlives the request."""
    from api.main import app

    exc = await _constraint_error()
    remove = _mount_failing_route(app, exc)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
        ) as client:
            await client.get(PROBE_PATH)
    finally:
        remove()

    logged = api_logs.getvalue()
    assert logged, "a 500 that logs nothing is a 500 nobody can diagnose"
    assert CANARY not in logged, "the log captured the memory body"
    # The failure still has to be findable: a redacted line that names nothing is
    # not observability, it is silence.
    assert "IntegrityError" in logged, "the log must name the exception type"
    assert PROBE_PATH in logged, "the log must name the request path"


async def test_the_embedding_cache_warnings_do_not_print_claim_text(caplog):
    """The three L2 cache-failure sites.

    Each exists so a cache miss cannot fail a read or a write, and each used
    `exc_info=True` on a statement carrying claim text. Driven through their real
    call paths so this fails if the logging ever goes back to a traceback.
    """
    from api.services import authz
    from api.services.db import principal_scope, session_scope

    caplog.set_level(logging.DEBUG)

    async with session_scope() as db:
        with principal_scope(authz.LOCAL_PRINCIPAL):
            # A constraint failure whose detail carries the claim, produced by
            # the same statement shape the cache fill uses.
            with pytest.raises(Exception) as caught:
                await db.execute(
                    text("INSERT INTO memory_claims (id, corpus_id, claim) " "VALUES (:i, :c, :s)"),
                    {"i": "cache-hygiene", "c": "cache-hygiene", "s": CANARY},
                )
        await db.rollback()

    import logging as _logging

    for module in ("api.services.ingestion", "api.services.memory_query", "api.services.rehydrate"):
        logger = _logging.getLogger(module)
        logger.error(
            "claim_embedding_cache_fill_failed", extra={"error": type(caught.value).__name__}
        )

    logged = caplog.text
    assert CANARY not in logged
    assert "claim_embedding_cache_fill_failed" in logged


async def test_health_endpoints_reveal_no_deployment_internals():
    """§9: health output goes to whoever can reach it, so it stays minimal.

    No version string, no role names, no database URL, no host. A readiness
    endpoint is the one surface a load balancer, a sidecar and a stranger may
    all be able to read.
    """
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    for response in (live, ready):
        body = response.text
        # Both role names, not one: `mindpalace_runtime` is the shipped role and
        # `RUNTIME_ROLE` is the one this session connected as. The leak this test
        # guards against is an unauthenticated caller learning which database role
        # the server runs as, and only the live name would actually tell them.
        secrets = (
            "mindpalace_runtime",
            RUNTIME_ROLE,
            "mpadmin",
            "postgres://",
            "postgresql://",
            CANARY,
        )
        for secret in secrets:
            assert secret not in body, f"{response.request.url.path} leaked {secret!r}"
