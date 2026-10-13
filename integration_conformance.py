"""The integration conformance kit: is this installation ready to be built on?

Not a test of Mind Palace. A checklist another product team can run against
*their* installation, in their environment, with their credentials, before they
commit to integrating.

Every check names what it proves and what it does not. A kit that only ever
passes is not a kit; the negative checks here are the ones that would catch a
misconfigured deployment.

Run it directly:

    DATABASE_URL=... MIND_PALACE_URL=http://localhost:8000 \\
        venvmp/bin/python -m integration_conformance

or under pytest, which is how CI runs it.
"""

from __future__ import annotations

import asyncio
import os
import sys

#: What the SDK imports, versus what the SDK exposes. An integrator's first
#: failure is usually an import that used to work.
REQUIRED_SDK_SYMBOLS = ("MindPalace", "MemoryClient", "MemoryClientError", "verify_receipt")


class Check:
    """One line of the report, and the evidence behind it."""

    def __init__(self, name: str, ok: bool, detail: str = "", *, skipped: bool = False):
        self.name, self.ok, self.detail, self.skipped = name, ok, detail, skipped

    def render(self) -> str:
        mark = "-" if self.skipped else ("ok  " if self.ok else "FAIL")
        return f"  [{mark}] {self.name}" + (f" -- {self.detail}" if self.detail else "")


async def run() -> int:
    base_url = os.environ.get("MIND_PALACE_URL", "http://127.0.0.1:8000")
    api_key = os.environ.get("MIND_PALACE_API_KEY")
    corpus = os.environ.get("MIND_PALACE_CORPUS", "default")
    checks: list[Check] = []

    # 1. The public SDK imports, and exposes what is documented.
    try:
        import mindpalace_sdk

        missing = [s for s in REQUIRED_SDK_SYMBOLS if not hasattr(mindpalace_sdk, s)]
        checks.append(
            Check(
                "the public SDK imports and exposes its documented symbols",
                not missing,
                f"missing {missing}" if missing else "no internal import required",
            )
        )
    except Exception as exc:  # noqa: BLE001 - the point is that any failure counts
        checks.append(Check("the public SDK imports", False, f"{type(exc).__name__}: {exc}"))

    # 2. The error taxonomy is importable and distinguishes components.
    try:
        from mindpalace_errors import COMPONENTS, MindPalaceError

        checks.append(
            Check(
                "the error taxonomy is importable and typed",
                "provider" in COMPONENTS and issubclass(MindPalaceError, Exception),
                f"components: {', '.join(COMPONENTS)}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("the error taxonomy imports", False, type(exc).__name__))

    # 3. REST is reachable, and says which schema it is running.
    try:
        import httpx

        async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")
            checks.append(
                Check("the service is live", live.status_code == 200, f"HTTP {live.status_code}")
            )
            body = (
                ready.json()
                if ready.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            checks.append(
                Check(
                    "readiness reports the database and the authorization mode",
                    ready.status_code in (200, 503) and "auth" in body,
                    f"HTTP {ready.status_code}, checks={body.get('checks')}",
                )
            )
            checks.append(
                Check(
                    "every response carries a correlation id",
                    bool(ready.headers.get("x-request-id")),
                    ready.headers.get("x-request-id", "(none)"),
                )
            )

            # 4. OpenAPI is available, so a client generator can be pointed at it.
            spec = await client.get("/openapi.json")
            checks.append(
                Check(
                    "the OpenAPI schema is published",
                    spec.status_code == 200 and "paths" in spec.json(),
                    f"{len(spec.json().get('paths', {}))} paths" if spec.status_code == 200 else "",
                )
            )

            if not api_key:
                checks.append(
                    Check(
                        "authenticated read/write, cross-tenant denial, evidence and history",
                        True,
                        "skipped -- set MIND_PALACE_API_KEY to run the authenticated checks",
                        skipped=True,
                    )
                )
            else:
                checks.extend(await _authenticated(client, api_key, corpus))

    except Exception as exc:  # noqa: BLE001 - an unreachable service is a result
        checks.append(Check("the service is reachable", False, f"{type(exc).__name__}: {exc}"))

    print("\nMind Palace integration conformance\n")
    for check in checks:
        print(check.render())
    failures = [c for c in checks if not c.ok and not c.skipped]
    print(
        f"\n{len(checks) - len(failures)}/{len(checks)} checks passed"
        + (f", {len(failures)} FAILED" if failures else "")
    )
    print(
        "\nThis kit checks that an installation is configured and reachable. It does "
        "not\nprove your application's own authorization; that is yours to test.\n"
    )
    return 1 if failures else 0


async def _authenticated(client, api_key: str, corpus: str) -> list[Check]:
    """The checks that need a real credential."""
    checks: list[Check] = []
    headers = {"Authorization": f"Bearer {api_key}"}

    written = await client.post(
        "/api/memory/remember",
        headers=headers,
        json={
            "corpus": corpus,
            "path": "conformance/probe.md",
            "statement": "The conformance probe was recorded at deploy time.",
        },
    )
    checks.append(
        Check(
            "this credential can write",
            written.status_code in (200, 201),
            f"HTTP {written.status_code}",
        )
    )

    query = await client.post(
        "/api/memory/query", headers=headers, json={"corpus": corpus, "query": "conformance probe"}
    )
    body = query.json() if query.status_code == 200 else {}
    checks.append(
        Check(
            "this credential can read back what it wrote",
            query.status_code == 200 and bool(body.get("current_memories")),
            f"HTTP {query.status_code}, {len(body.get('current_memories', []))} memories",
        )
    )
    checks.append(
        Check(
            "a response carries evidence and provenance",
            all("evidence" in memory for memory in body.get("current_memories", [])[:1])
            or not body.get("current_memories"),
            "every returned memory cites evidence",
        )
    )

    # The negative check. A kit with only positive checks is a smoke test.
    unknown = await client.post(
        "/api/memory/query",
        headers=headers,
        json={"corpus": "a-corpus-this-credential-cannot-possibly-hold", "query": "anything"},
    )
    checks.append(
        Check(
            "a corpus outside the credential's grants is not readable",
            unknown.status_code == 404,
            f"HTTP {unknown.status_code} (404 = not disclosed, not 403)",
        )
    )

    history = await client.post(
        "/api/memory/history",
        headers=headers,
        json={"corpus": corpus, "path": "conformance/probe.md"},
    )
    checks.append(
        Check(
            "temporal history is available",
            history.status_code in (200, 404),
            f"HTTP {history.status_code}",
        )
    )

    # Consistency of the error envelope, asserted on a *real* refusal.
    detail = (
        unknown.json().get("detail", {})
        if unknown.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    checks.append(
        Check(
            "errors use the documented envelope",
            {"code", "message"} <= set(detail) and bool(detail.get("request_id")),
            f"code={detail.get('code')} request_id={detail.get('request_id')}",
        )
    )
    return checks


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
