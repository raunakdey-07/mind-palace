# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Request-scoped authentication for the HTTP surface.

Authentication is a middleware, not a per-route dependency, and that is a
deliberate choice. A dependency runs only for routes that declare it, so the
next route added without it would be an unauthenticated route -- and nothing
would say so until it leaked. A middleware runs for every request the process
serves, so forgetting to protect a new endpoint is not possible.

The middleware also owns the teardown. A principal left in a `ContextVar` would
be inherited by the next request served on the same task, so request N+1 would
be scoped to request N's credential. Exiting the scope in a `finally` is what
makes "one request, one identity" true rather than merely intended;
`tests/test_tenant_isolation.py` asserts it by interleaving two identities
through one client.
"""

from __future__ import annotations

from api.services.authz import Principal
from api.services.db import current_principal


def request_principal(request) -> Principal:
    """The identity resolved for this request.

    Reads the `ContextVar` rather than request state so that a route handler, a
    service several layers down, and a background task started from the handler
    all see the same identity, and all of them see the same one.
    """
    return current_principal()
