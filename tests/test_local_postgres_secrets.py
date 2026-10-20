"""The compose file's defaults must not be a server deployment's secrets.

`docker-compose.yml` needs defaults, because `podman-compose` evaluates a
required variable eagerly across every service: without them, the `backend`
service's secret stops the `postgresql` service from starting, even though
that service has no secret in it. A default is right for a developer's
machine.

It is not right for a service on a network, and the place that knows the
difference is the server entrypoint -- not the compose file, which cannot
tell a local run from a deployment. So the entrypoint refuses to start when
the value is the local-development default.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def server_env(monkeypatch):
    """A configuration that is otherwise ready to serve."""
    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "server")
    monkeypatch.setenv("MIND_PALACE_AUTH_MODE", "required")
    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", "/content")
    monkeypatch.setenv("MIND_PALACE_RUNTIME_PASSWORD", "a-real-password")
    monkeypatch.setenv("MIND_PALACE_CURSOR_SECRET", "a-real-cursor-secret-" + "x" * 32)
    return monkeypatch


def test_a_local_deployment_may_use_the_defaults(monkeypatch):
    """The developer's machine is the case the defaults are for.

    `MIND_PALACE_DEPLOYMENT=local` is the documented single-user workflow. A
    placeholder secret there is no worse than the documented
    `postgresql://mpadmin:secret@localhost:5432/mindpalace` the README
    already tells people to use.
    """
    from api.services.authz import server_mode_errors

    monkeypatch.setenv("MIND_PALACE_DEPLOYMENT", "local")
    monkeypatch.setenv("MIND_PALACE_RUNTIME_PASSWORD", "mindpalace-local-runtime-password")
    monkeypatch.setenv(
        "MIND_PALACE_CURSOR_SECRET",
        "mindpalace-local-dev-cursor-secret-00000000000000000000",
    )

    assert server_mode_errors() == []


def test_a_server_deployment_refuses_the_runtime_password_default(server_env):
    """The password the compose file falls back to is not a deployment secret.

    It is printed in the file, so it is known to everybody who has read the
    repository. A server that accepts it accepts a credential that is not
    one.
    """
    from api.services.authz import server_mode_errors

    server_env.setenv("MIND_PALACE_RUNTIME_PASSWORD", "mindpalace-local-runtime-password")

    problems = server_mode_errors()

    assert any("MIND_PALACE_RUNTIME_PASSWORD" in p for p in problems), problems


def test_a_server_deployment_refuses_the_cursor_secret_default(server_env):
    """The cursor secret signs the change feed's opaque cursors.

    A known signing key means a cursor anyone can mint, which is a cursor
    that carries no authority.
    """
    from api.services.authz import server_mode_errors

    server_env.setenv(
        "MIND_PALACE_CURSOR_SECRET",
        "mindpalace-local-dev-cursor-secret-00000000000000000000",
    )

    problems = server_mode_errors()

    assert any("MIND_PALACE_CURSOR_SECRET" in p for p in problems), problems


def test_a_server_deployment_with_real_secrets_is_allowed(server_env):
    """The control: the check refuses the placeholders, not the setting.

    Without this, a check that always fires would look identical to one that
    works.
    """
    from api.services.authz import server_mode_errors

    assert server_mode_errors() == []
