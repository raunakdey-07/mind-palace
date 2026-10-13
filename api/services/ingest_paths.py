# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Which directories a client is allowed to name.

`POST /api/ingest/repo` and `POST /api/corpora/{name}/sync` both take a path on
the *server's* filesystem and read it. That is an operator capability exposed
over HTTP, and it means something different once the service is shared: an
authenticated caller of one tenant can ask the service to walk a directory it was
never given and read the Markdown in it into a corpus they do own.

The limits are narrow — `*.md` only, and no more than the caller could already
write — but "narrow" is not "safe", and it is the kind of gap that reads as
unremarkable in a review because there is a corpus check on the same route.

So the boundary is explicit and it fails closed:

* `MIND_PALACE_INGEST_ROOTS` is a path-separator-separated list of directories a
  client may name. A path that does not resolve inside one of them is refused
  before a single file is read.
* Unset, it means "anywhere on this filesystem", which preserves what the local
  CLI and a single-operator deployment have always been able to do.
* `MIND_PALACE_DEPLOYMENT=server` refuses to start without it, for the same
  reason it refuses to start without authentication: a convenient local default
  must not become an unauthenticated-looking shared service by accident.

Resolution is done with `Path.resolve()`, so `..` segments and symlinks are
followed to their real target *before* the check. Comparing the strings instead
is the classic mistake — `/data/../etc` and a symlink out of `/data` both pass a
prefix test and are not in `/data`.
"""

from __future__ import annotations

import os
from pathlib import Path


class IngestPathRefused(ValueError):
    """The requested directory is outside what this deployment allows."""


def allowed_roots() -> tuple[Path, ...]:
    """Directories a client may name, from `MIND_PALACE_INGEST_ROOTS`."""
    raw = os.getenv("MIND_PALACE_INGEST_ROOTS", "").strip()
    if not raw:
        return ()
    return tuple(
        Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()
    )


#: The value that turns filesystem ingestion off entirely, for a deployment that
#: reaches Mind Palace only over HTTP and never asks it to read a directory.
#:
#: Without this, `server` mode refused to start unless `MIND_PALACE_INGEST_ROOTS`
#: was set -- which made the filesystem-ingestion setting a precondition for
#: *starting*, including for an API-only deployment that never touches those two
#: routes. That is the wrong shape: the risk is the routes, so the control belongs
#: on the routes, not on the process. Setting this to `none` disables them and is
#: accepted in place of a root list.
DISABLED = "none"


def disabled() -> bool:
    """Whether this deployment has switched filesystem ingestion off."""
    return os.getenv("MIND_PALACE_INGEST_ROOTS", "").strip().lower() == DISABLED


def require_permitted(candidate: str) -> Path:
    """Resolve `candidate` and refuse it unless it sits inside an allowed root.

    Returns the resolved path so the caller uses the real one rather than
    re-resolving a string it has already been told about.

    Raises `IngestPathRefused` rather than returning a reason, so the message
    cannot leak the allowed roots to a caller who is not entitled to know them.
    """
    resolved = Path(candidate).expanduser().resolve()
    if disabled():
        raise IngestPathRefused(
            "filesystem ingestion is disabled on this deployment " "(MIND_PALACE_INGEST_ROOTS=none)"
        )
    roots = allowed_roots()
    if roots and any(resolved == root or root in resolved.parents for root in roots):
        return resolved
    if not roots:
        return resolved
    raise IngestPathRefused(
        "path is outside MIND_PALACE_INGEST_ROOTS; this deployment only ingests "
        "from configured directories"
    )


def server_mode_errors() -> list[str]:
    """Why a server deployment may not start, empty if it may.

    Called from the same place the authentication check is, because it is the same
    class of problem: a capability that is fine on one developer's laptop and a
    liability on a shared network, and which nothing else would notice is
    missing.
    """
    from api.services.authz import DEPLOYMENT_SERVER, deployment_mode

    if deployment_mode() != DEPLOYMENT_SERVER:
        return []
    if allowed_roots() or disabled():
        return []
    return [
        "MIND_PALACE_DEPLOYMENT=server requires MIND_PALACE_INGEST_ROOTS: set it to "
        "the directories ingestion may read, or to 'none' to disable the two routes "
        "that read the server filesystem. Without either, a caller can ask the "
        "service to read any directory the process can see"
    ]
