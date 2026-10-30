# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Mind Palace FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import logging
import os

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.exc import SQLAlchemyError

from api.routers import context, corpora, ingest, memory, query, search
from api.services.authz import (
    DEPLOYMENT_SERVER,
    AuthorizationError,
    assert_server_configuration,
    deployment_mode,
    describe as describe_authz,
)
from api.services.db import principal_scope
from api.services.posture import inspect as inspect_posture
from mindpalace_errors import MindPalaceError

logger = logging.getLogger("mindpalace.api")


def create_app() -> FastAPI:
    """Build the application, refusing a configuration that must not serve.

    `MIND_PALACE_DEPLOYMENT=server` plus `MIND_PALACE_AUTH_MODE=off` is an
    unauthenticated service on a network, and it is exactly what a default
    `uvicorn api.main:app` or `docker compose up` produces. Exiting with a reason
    here is louder than a log line in a container nobody tails.

    The database posture is *not* checked here: it needs a connection, and
    importing this module must not require one. It is checked in the lifespan,
    before the server accepts a request -- see `enforce_startup_posture`.
    """
    assert_server_configuration()
    return FastAPI(
        title="Mind Palace API",
        description="The Durable AI Memory Substrate",
        version=importlib.metadata.version("mindpalace-os"),
        lifespan=_lifespan,
    )


async def _probe_scope_token(db) -> tuple[bool, bool, bool] | None:
    """Probe scope-token enforcement via `mp_scope_enforcement_status`.

    Computes the token this process would send (HMAC-SHA256 of the probe scope
    under `MIND_PALACE_SCOPE_SECRET`, the same inputs `_apply_tenant_scope`
    uses) and asks the database whether it verifies.

    Returns ``(enforced, using_default_secret, secret_matches)``, or ``None``
    when the probe is unavailable -- the function is missing on an old schema,
    or the database cannot be reached. A ``None`` probe is treated as fail
    closed: the caller must refuse.
    """
    import hashlib
    import hmac
    import os

    from sqlalchemy import text as _text

    secret = os.getenv("MIND_PALACE_SCOPE_SECRET", "")
    probe_scope = "{*}"
    probe_token = hmac.new(secret.encode(), probe_scope.encode(), hashlib.sha256).hexdigest()
    try:
        row = (
            await db.execute(
                _text(
                    "SELECT enforced, using_default_secret, secret_matches "
                    "FROM mp_scope_enforcement_status(:t, :s)"
                ),
                {"t": probe_token, "s": probe_scope},
            )
        ).first()
    except Exception:  # noqa: BLE001 - probe unavailable is fail-closed, not a pass
        return None
    if row is None:
        return None
    return bool(row[0]), bool(row[1]), bool(row[2])


_SCOPE_ROTATION_ORDER = (
    "Rotate in this order: (1) set the new secret in the database with "
    "`mindpalace secret rotate --out FILE` as mindpalace_owner, "
    "(2) update MIND_PALACE_SCOPE_SECRET in the service environment to match, "
    "(3) restart the service. Expect a brief outage."
)


async def _enforce_scope_token_startup() -> None:
    """Scope-token enforcement for server-profile startup (Migration 015).

    Local deployments are exempt. Raises RuntimeError with a message naming the
    cause and the safe rotation order when enforcement is on and the probe does
    not verify. Never prints any secret value.
    """
    import os

    from api.services import authz
    from api.services.db import session_scope

    mode = authz.deployment_mode()
    if mode != authz.DEPLOYMENT_SERVER:
        return

    if os.getenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off").lower() == "off":
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "MIND_PALACE_ENFORCE_SCOPE_TOKEN=off: scope-token enforcement is DISABLED; "
            "tenant scope can be forged. Run mp_set_scope_token_enforcement(true) to close it."
        )
        if os.getenv("MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY", "") != "1":
            raise RuntimeError(
                "refusing to start: scope-token enforcement is disabled (off). "
                "Set MIND_PALACE_I_UNDERSTAND_SCOPE_FORGERY=1 to acknowledge, "
                "or run mp_set_scope_token_enforcement(true)."
            )
        return

    secret = os.getenv("MIND_PALACE_SCOPE_SECRET", "")
    if secret == "" or secret == "dev-scope-secret":
        raise RuntimeError(
            "refusing to start: MIND_PALACE_SCOPE_SECRET is unset or uses the "
            "default dev value. " + _SCOPE_ROTATION_ORDER
        )

    try:
        async with session_scope() as db:
            probed = await _probe_scope_token(db)
    except Exception as exc:  # noqa: BLE001 - a probe that cannot run is not a pass
        raise RuntimeError(
            "refusing to start: could not verify scope enforcement "
            f"({type(exc).__name__}). Start with "
            "MIND_PALACE_DEPLOYMENT=local to run without this check."
        ) from exc

    if probed is None:
        raise RuntimeError(
            "refusing to start: scope-token probe unavailable "
            "(mp_scope_enforcement_status missing or unreachable). "
            "Apply migration 015, or start with "
            "MIND_PALACE_DEPLOYMENT=local to run without this check."
        )
    enforced, using_default_secret, secret_matches = probed

    if not enforced:
        raise RuntimeError(
            "refusing to start: scope-token enforcement is disabled in the "
            "database. Run SELECT mp_set_scope_token_enforcement(true) as "
            "mindpalace_owner. " + _SCOPE_ROTATION_ORDER
        )
    if using_default_secret:
        raise RuntimeError(
            "refusing to start: the database still uses the default scope "
            "secret. Rotate it first. " + _SCOPE_ROTATION_ORDER
        )
    if not secret_matches:
        raise RuntimeError(
            "refusing to start: MIND_PALACE_SCOPE_SECRET disagrees with the "
            "database row. " + _SCOPE_ROTATION_ORDER
        )


async def enforce_startup_posture() -> None:
    """Refuse to serve a server deployment whose database role is unsafe.

    Runs in the lifespan, so it happens after the process has its configuration
    and its connection but before uvicorn reports itself as accepting traffic.
    That ordering is the point: the alternatives were refusing only at
    `/health/ready`, which leaves a window where a load balancer is sending
    requests to a service that was never safe, or refusing at import, which
    would make the module unusable for offline verification.

    Local deployments are exempt, deliberately. The documented five-minute path
    runs as the database user that set the schema up -- frequently the migration
    role -- and enforcing a least-privileged runtime posture there would refuse
    to start on a machine where nothing is shared. The check is about what is
    exposed to a network, and only the operator knows that.

    Raises rather than logging. A container that starts, logs a warning nobody
    reads, and serves every tenant to every caller is the failure this whole
    mechanism exists to prevent.
    """
    from api.services import authz
    from api.services.db import session_scope
    from api.services.posture import inspect

    problems = authz.server_mode_errors()
    if problems:
        raise RuntimeError(
            "refusing to start: "
            + "; ".join(problems)
            + ". See docs/security.md for what a network deployment must set."
        )
    try:
        mode = authz.deployment_mode()
    except ValueError as exc:  # already refused above, but never assume
        raise RuntimeError(f"refusing to start: {exc}") from exc
    if mode != authz.DEPLOYMENT_SERVER:
        return

    try:
        async with session_scope() as db:
            posture = await inspect(db)
    except Exception as exc:  # noqa: BLE001 - a posture that cannot be read is not a pass
        raise RuntimeError(
            "refusing to start: MIND_PALACE_DEPLOYMENT=server could not verify the "
            f"database role ({type(exc).__name__}). Start with "
            "MIND_PALACE_DEPLOYMENT=local to run without this check."
        ) from exc
    unsafe = posture.server_problems()
    if unsafe:
        raise RuntimeError(
            "refusing to start: "
            + "; ".join(unsafe)
            + ". See docs/security.md for what a network deployment must set."
        )

    await _enforce_scope_token_startup()


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    await enforce_startup_posture()
    yield


app = create_app()


# Authentication is middleware rather than a per-route dependency. A dependency
# runs only where it is declared, so the next route added without it would be an
# unauthenticated route and nothing would say so. This runs for every request.
#
# It is registered *before* CORS and the metrics instrumentator below, so a
# rejected request never reaches a handler and never reaches an exporter. Health
# and metrics are excluded explicitly: they are operational endpoints about this
# process, not about tenant data, and an operator must be able to see that the
# service is alive while it is refusing traffic.
_UNAUTHENTICATED_PREFIXES = ("/health", "/metrics", "/openapi.json", "/docs", "/redoc")


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    if request.url.path.startswith(_UNAUTHENTICATED_PREFIXES):
        return await call_next(request)

    from api.services.db import session_scope

    try:
        async with session_scope() as db:
            from api.services.authz import resolve_principal

            principal = await resolve_principal(db, request.headers.get("authorization"))
    except AuthorizationError as exc:
        # Refused before any handler runs, and before any tenant data is read.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        # The same translator the routes use, so an authentication-phase database
        # failure and a handler-phase one are indistinguishable to a caller. A
        # client must not be able to tell which half of the request failed, and a
        # sanitized message must not leak the database URL or a host.
        from api.services.memory_public import translate_database_error

        translated = translate_database_error(exc)
        return JSONResponse(
            status_code=translated.status_code,
            content={"detail": {"code": translated.code, "message": translated.message}},
        )

    # The scope is entered here and exited in the `finally`, so a handler that
    # raises, a client that disconnects, and a cancellation all leave the next
    # request with no inherited identity.
    with principal_scope(principal):
        return await call_next(request)


# Registered *after* `authenticate_request` on purpose: Starlette wraps the most
# recently added middleware outermost, and this one has to be outside the
# authentication middleware. A refused request returns from `authenticate_request`
# without ever calling `call_next`, so a correlation id set inside it would never
# be minted for exactly the requests a user is about to report.
@app.middleware("http")
async def identify_request(request: Request, call_next):
    """Give every request a correlation id, and return it to the caller.

    An inbound `X-Request-Id` is honoured rather than overwritten, so an id
    minted by a load balancer, a gateway, or the caller's own tracing survives
    the whole path. It is treated as an opaque string and never parsed, so a
    hostile header cannot smuggle control characters into a log line: anything
    that is not a short printable token is replaced.
    """
    from mindpalace_errors import RequestId

    inbound = request.headers.get("x-request-id", "")
    request_id = inbound if _is_safe_request_id(inbound) else RequestId.ensure()
    RequestId.set(request_id)
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


def _is_safe_request_id(value: str) -> bool:
    """Accept only a short printable token, so a header cannot forge a log line."""
    return bool(value) and len(value) <= 64 and value.isprintable()


@app.exception_handler(AuthorizationError)
async def authorization_error(request: Request, exc: AuthorizationError):
    """Render a refusal raised below the middleware, with the same shape.

    A `403` here is always a *role* refusal for a corpus the caller already
    proved it may name. A corpus outside the caller's scope raises before the
    query and is rendered as `404` by the choke point, so a scoped-out corpus
    never becomes visible as a distinguishable status.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _detail(exc.code, exc.message)},
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
    )


@app.exception_handler(MindPalaceError)
async def taxonomy_error(request: Request, exc: MindPalaceError):
    """Render any taxonomy failure with its own status and retryability.

    The existing `detail` shape is preserved verbatim -- `code` and `message`,
    unchanged -- and the new fields are added *beside* it. An integrator who has
    already written `body["detail"]["code"]` keeps working; one who wants to know
    whether to retry reads `retryable`, and one reporting a problem reads
    `request_id`.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})


def _detail(code: str, message: str) -> dict:
    """The legacy `detail` object, plus the request id.

    Additive on purpose. The two-field shape is what the documented failure
    contract already is, and quietly replacing it would break every integrator
    who reads `code` from it.
    """
    from mindpalace_errors import RequestId

    return {"code": code, "message": message, "request_id": RequestId.ensure()}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    memory_ask = (
        request.url.path in {"/api/query/ask", "/api/query"}
        and isinstance(exc.body, dict)
        and exc.body.get("mode", "rag") != "rag"
    )
    if request.url.path.startswith("/api/memory/") or memory_ask:
        # Do not echo untrusted inputs or non-JSON-serializable validator contexts.
        message = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={"detail": _detail("invalid_request", message)},
        )
    return await request_validation_exception_handler(request, exc)


class RedactFailures:
    """Stop an unhandled error from reaching the server's error log.

    This exists to keep customer memory out of the log, and the reason is not
    stylistic. PostgreSQL puts the offending row in the error `DETAIL` for every
    constraint violation --

        DETAIL:  Failing row contains (..., "a private fact about someone's
                 medical appointment", ...)

    -- and asyncpg passes that string through verbatim. SQLAlchemy's
    `hide_parameters` does not help: it suppresses *bound parameters* in the
    rendered SQL, and this text comes from the server, not from the statement. Any
    traceback that prints the exception therefore writes memory content to the
    log, and an operator's log aggregator keeps it long after the request.

    An `@app.exception_handler(Exception)` does **not** fix that. Starlette's
    `ServerErrorMiddleware` sends the handler's response and then re-raises
    anyway, explicitly "so servers can log the error" -- the response is redacted
    but the traceback still reaches uvicorn. Catching here, inside that
    middleware, is what actually stops the propagation.

    What it covers, precisely: errors raised while authenticating a request and
    while serving one. What it does not: CORS and the Prometheus wrapper, which
    sit outside it and carry no request data.

    The identifying fields go into the message rather than into `extra`, because
    the default log formatter renders only the message and an operator reading
    plain text would otherwise get a line saying `unhandled_error` and nothing
    else.

    The cost is real and worth stating: a full traceback no longer appears in the
    server log. Debugging a 500 means reproducing it, where `echo=True` and a
    debugger still show everything. That trade is the right way round for a
    system whose whole purpose is storing what people told it in confidence.

    One case is deliberately not covered. If the response has already started --
    a streaming endpoint that fails midway -- no body can be replaced, so the
    exception is re-raised and may be logged. Every response this service produces
    is built before it starts, so that path is not reachable here today, and
    pretending otherwise would be worse than the leak: it would hide the fact
    that the guarantee has a boundary.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:  # noqa: BLE001 - this is the catch-all by design
            if started:
                raise
            # A taxonomy failure that reached here has no handler above it, so it
            # gets its own status and its own retryability rather than being
            # flattened into a 500. The `detail` shape is identical either way.
            from mindpalace_errors import envelope

            body = envelope(exc)
            logger.error(
                "unhandled_error method=%s path=%s error=%s code=%s request_id=%s",
                scope.get("method"),
                scope.get("path"),
                type(exc).__name__,
                body["code"],
                body["request_id"],
            )
            status = exc.status_code if isinstance(exc, MindPalaceError) else 500
            response = JSONResponse(
                status_code=status,
                content={"detail": body},
                headers={"X-Request-Id": body["request_id"]},
            )
            await response(scope, receive, send)


# Inside CORS and the metrics instrumentator, outside the authentication
# middleware and the application. That is deliberate and it is exactly the scope
# claimed: an error raised while authenticating a request, or while serving one,
# is the path a memory body or a corpus name would travel, and all of those are
# caught here. An exception raised by CORS itself or by the Prometheus wrapper
# would not be, and those carry no request data. Making this outermost instead
# would mean adding it after `Instrumentator().instrument(app)`, which is a
# library call that may add its own middleware at any time -- a guarantee that
# depends on a third party's ordering is not a guarantee.
app.add_middleware(RedactFailures)


# CORS — allow the Next.js frontend in dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    from sqlalchemy import text

    from api.services.db import session_scope

    timeout_seconds = float(os.getenv("MIND_PALACE_READINESS_TIMEOUT_SECONDS", "2"))
    auth = describe_authz()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with session_scope() as db:
                result = await db.execute(
                    text(
                        "SELECT to_regclass('corpora') IS NOT NULL, "
                        "to_regclass('memory_documents') IS NOT NULL, "
                        "to_regclass('memory_versions') IS NOT NULL"
                    )
                )
                if not all(result.one()):
                    return JSONResponse(
                        status_code=503,
                        content={"status": "not_ready", "checks": {"database": "unavailable"}},
                    )
                # Does this connection actually get the policies? A role that
                # bypasses them makes `auth.required: true` a claim the database
                # does not honour, and readiness is where an operator looks.
                #
                # The verdict is reported; the inventory is not. This endpoint is
                # read by whatever probes the service -- a load balancer, a
                # sidecar, a health dashboard, and in a misconfigured deployment
                # anything else that can reach the port -- and it used to answer
                # with the connected role's name, whether it was a superuser,
                # whether it held `BYPASSRLS`, whether it could enumerate
                # credential hashes, and which archive tables it could `DELETE`.
                # None of that belongs in an anonymous response, and the half that
                # is useful -- the detail -- is what `mindpalace posture` prints
                # for an operator who already holds database credentials.
                #
                # The verdict is only a verdict in `server` mode. In `local` there
                # is no second tenant to be isolated from, the documented workflow
                # is a single developer connecting as their own superuser, and
                # answering `not_ready` to that would break the convenient path
                # for a problem that cannot occur there. The same rule gates every
                # other `server` requirement, including refusing to start.
                posture = await inspect_posture(db)
                policy_enforced = not posture.problems()
                enforced = policy_enforced or deployment_mode() != DEPLOYMENT_SERVER
                # Scope-token enforcement (Migration 015): in server mode, when
                # enforcement is on, the token this process would send must
                # verify against the database. A mismatch or an unavailable
                # probe makes the service not-ready; enforcement off is not a
                # readiness failure here (startup already refuses a server that
                # runs enforcement-off without acknowledgement).
                if (
                    deployment_mode() == DEPLOYMENT_SERVER
                    and os.getenv("MIND_PALACE_ENFORCE_SCOPE_TOKEN", "off").lower() == "on"
                ):
                    probed = await _probe_scope_token(db)
                    if probed is None or not probed[2]:
                        return JSONResponse(
                            status_code=503,
                            content={
                                "status": "not_ready",
                                "checks": {
                                    "database": "ok",
                                    "row_level_security": "enforced",
                                    "scope_token": "mismatch",
                                },
                                "auth": auth,
                                "reason": (
                                    "MIND_PALACE_SCOPE_SECRET disagrees with the "
                                    "database row, or the scope-token probe is "
                                    "unavailable. Rotate the secret in the database "
                                    "first, then in the service environment, then "
                                    "restart."
                                ),
                            },
                        )
    except (SQLAlchemyError, OSError, TimeoutError, ValueError):
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": {"database": "unavailable"}},
        )

    if not enforced:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "checks": {"database": "ok", "row_level_security": "not_enforced"},
            },
        )

    return JSONResponse(
        status_code=200,
        # The authorization mode travels with readiness on purpose. An operator
        # who cannot tell `off` from `required` cannot tell a tenant-safe
        # deployment from one that will answer anyone who asks. It is one boolean
        # about this service's own behaviour, not a description of the database.
        content={
            "status": "ready",
            "checks": {"database": "ok", "row_level_security": "enforced"},
            "auth": auth,
        },
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Register routers
app.include_router(corpora.router, prefix="/api/corpora", tags=["corpora"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(context.router, prefix="/api/context", tags=["context"])
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
