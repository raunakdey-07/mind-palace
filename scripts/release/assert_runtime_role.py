"""Assert the upgraded archive is still correct **as the runtime role**.

    python scripts/release/assert_runtime_role.py --superuser-url ... --runtime-url ...

## Why this is separate from `assert_upgrade.py`

That script proves the upgrade did not rewrite the archive, and it runs as a
superuser -- and a superuser bypasses row-level security entirely, so it cannot
see a policy that is keyed on the wrong column.

That is not a hypothetical defect class. This project's development database
carried exactly that bug for months: `USING
(mindpalace_allowed_corpus(id))` on `memory_documents`, whose column is
`corpus_id`. Every superuser test passed. The defect is only visible to a reader
who is subject to the policy, and only when a corpus id and a document id
disagree.

So this script runs a **real server** as `mindpalace_runtime` in
`MIND_PALACE_AUTH_MODE=required`, presents a **real credential**, and reads the
archive through the **public SDK** -- the same path a customer uses. The
credential's grants decide what the policies let through, so the policy column is
load-bearing rather than decorative.

## What it asserts, in order

1. With **no** credential, `GET /corpora` is refused. A server that serves data
   to an anonymous caller is a different product from the one documented.
2. A credential issued against **one** corpus sees **exactly** that corpus. This
   is the operator step: a credential is a tenant the moment it is issued.
3. After `credential grant`, the same reader sees **exactly** the corpora it was
   granted -- including `default` and the upgrade fixture.
4. `recall`, `explain` and `history` all work through that reader, and the
   v0.9.0 receipt still verifies offline.
5. The claim identities the runtime role sees are the **same** the superuser
   sees. A policy that hides rows shows up here; a policy keyed on the wrong
   column shows up in step 4, because `default`'s id matches no document id.

Steps 3 and 5 are what make this a *policy* test rather than a smoke test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# The repository root, because `mindpalace_sdk` is imported as a top-level module
# and imports `memory_receipt` the same way. Running this file by path puts
# `scripts/release` on the path instead, and the import fails there.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

PORT = 8931

#: Where the spawned server's output goes, so a startup failure can explain itself.
LOG_FILE = os.path.join(tempfile.gettempdir(), "mp-upgrade-runtime-server.log")


def run(command: list[str], env: dict | None = None) -> tuple[int, str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "MIND_PALACE_RUNTIME": "0", **(env or {})},
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--superuser-url", required=True)
    parser.add_argument("--runtime-url", required=True)
    parser.add_argument("--before", required=True, help="the v0.9.0 fixture directory")
    parser.add_argument("--issued-with", default="upgrade-scratch")
    parser.add_argument("--expect-corpora", default="default,upgrade-fixture")
    parser.add_argument("--question", default="What datastore does production use?")
    args = parser.parse_args()

    failures: list[str] = []

    # This process becomes the runtime role for its own engine. Set before any
    # `api` import, because the engine is built from `DATABASE_URL` at import
    # time and `admin_session_scope` opens its own engine from it.
    os.environ["DATABASE_URL"] = args.runtime_url
    os.environ["MIND_PALACE_AUTH_MODE"] = "required"
    os.environ["MIND_PALACE_DEPLOYMENT"] = "server"
    os.environ["MIND_PALACE_RUNTIME"] = "0"

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))
        if not ok:
            failures.append(f"{name}: {detail}")

    superuser = {
        "DATABASE_URL": args.superuser_url,
        "MIND_PALACE_AUTH_MODE": "off",
        "MIND_PALACE_DEPLOYMENT": "local",
    }
    expected = sorted(n for n in args.expect_corpora.split(",") if n)
    base = f"http://127.0.0.1:{PORT}"

    # -- 0. a scratch corpus, and a credential issued against only that one ----
    code, _ = run(
        [sys.executable, "-m", "cli.main", "corpora", "create", args.issued_with], superuser
    )
    if code != 0:
        code, _ = run(
            [sys.executable, "-m", "cli.main", "corpus", "create", args.issued_with], superuser
        )
    check(f"scratch corpus {args.issued_with} exists", code == 0, f"exit={code}")

    # Not `--quiet`: the secret is only printed in the full output, and so is the
    # credential ID that `credential grant` takes. Two facts, one command.
    code, output = run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "credential",
            "issue",
            "upgrade-safety",
            "--corpus",
            args.issued_with,
        ],
        superuser,
    )
    credential = _field(output, "secret")
    credential_id = _field(output, "id")
    check(
        "a credential could be issued for the runtime reader",
        code == 0 and credential.startswith("mpk_") and bool(credential_id),
        f"exit={code} secret={credential[:8]!r} id={credential_id!r}",
    )
    if not credential or not credential_id:
        print("\ncannot continue without a credential")
        return 1

    # -- 1. the server, as the least-privileged role --------------------------
    server_env = {
        "DATABASE_URL": args.runtime_url,
        "MIND_PALACE_AUTH_MODE": "required",
        "MIND_PALACE_DEPLOYMENT": "server",
        "MIND_PALACE_RUNTIME": "0",
        # Required in server mode, and the refusal is correct: without it a
        # caller can ask the service to read any directory the process can see.
        # This gate reads no files, so the two filesystem routes are disabled.
        "MIND_PALACE_INGEST_ROOTS": "none",
    }
    # The server's own stderr goes to a file, never to DEVNULL.
    #
    # It was DEVNULL, and the result was a CI failure that said only "nothing
    # answered on http://127.0.0.1:8931" -- with the actual reason, one line in
    # uvicorn's output, thrown away. A harness that hides the diagnosis of the
    # failure it is reporting costs the next person the whole debugging round
    # trip, and this one already cost one.
    server_log = open(LOG_FILE, "w+", encoding="utf-8")  # noqa: SIM115 - closed below
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        env={**os.environ, **server_env},
        stdout=server_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        if not _wait_for(base, 180):
            server_log.flush()
            check(
                "the runtime-role server started",
                False,
                f"nothing answered on {base}; the server said:\n{_tail(LOG_FILE)}",
            )
            return 1
        check("the server runs as mindpalace_runtime in required mode", True)

        # -- 2. no credential at all ----------------------------------------
        status, _ = _status(f"{base}/api/corpora")
        check("no credential is refused", status in (401, 403), f"GET /corpora -> {status}")

        # -- 3. before any grant: exactly the issued corpus ------------------
        from mindpalace_sdk import MindPalace  # noqa: E402 - after the env is set

        client = MindPalace(base_url=base, api_key=credential, runtime=False)
        listed = _corpora(base, credential)
        check(
            "before any grant the reader sees only its issued corpus",
            listed == [args.issued_with],
            f"listed={listed}",
        )

        # -- 4. after `credential grant`: exactly the granted corpora -------
        code, output = run(
            [
                sys.executable,
                "-m",
                "cli.main",
                "credential",
                "grant",
                credential_id,
                "--corpus",
                "default",
            ],
            superuser,
        )
        check("`credential grant default` succeeds", code == 0, output.strip()[:80])
        code, output = run(
            [
                sys.executable,
                "-m",
                "cli.main",
                "credential",
                "grant",
                credential_id,
                "--corpus",
                "upgrade-fixture",
            ],
            superuser,
        )
        check("`credential grant upgrade-fixture` succeeds", code == 0, output.strip()[:80])

        listed = _corpora(base, credential)
        wanted = sorted(set(expected) | {args.issued_with})
        check(
            "after the grant the reader sees exactly the granted corpora",
            listed == wanted,
            f"listed={listed} expected={wanted}",
        )

        # -- 5. the read surface, through the credential ---------------------
        for label, call in (
            ("recall", lambda: client.recall(args.question, corpus="upgrade-fixture")),
            ("explain", lambda: client.explain(args.question, corpus="upgrade-fixture")),
            ("history", lambda: client.history("infra.datastore", corpus="upgrade-fixture")),
        ):
            try:
                answer = call()
                body = json.dumps(
                    answer.__dict__ if hasattr(answer, "__dict__") else answer, default=str
                )
                check(
                    f"`{label}` works as the runtime role",
                    len(body) > 20 and "error" not in body[:200].lower(),
                    body[:90],
                )
            except Exception as exc:  # noqa: BLE001 - the point is to report anything
                check(
                    f"`{label}` works as the runtime role",
                    False,
                    f"{type(exc).__name__}: {exc}"[:110],
                )

        # -- 6. the old receipt, offline -------------------------------------
        code, output = run(
            [
                sys.executable,
                "-m",
                "memory_proof_cli",
                "verify",
                os.path.join(args.before, "receipt.json"),
            ],
            {"DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nothing"},
        )
        check(
            "the v0.9.0 receipt still verifies offline",
            code == 0 and "VERIFIED" in output.upper(),
            output.strip().replace("\n", " ")[:80],
        )

        # -- 7. and the runtime view equals the superuser view ---------------
        #     This one runs in-process rather than as a CLI command, and it has
        #     to: the superuser comparison needs the *scoped* principal, and a
        #     subprocess launched with `MIND_PALACE_AUTH_MODE=off` would be
        #     unrestricted -- its policy predicate returns TRUE for every row,
        #     so it would agree with the superuser by construction and prove
        #     nothing. Only a principal carrying this credential's corpus ids
        #     makes the policy column decide the answer.
        runtime_ids = _scoped_claim_ids(credential)
        super_ids = _superuser_claim_ids(args.superuser_url)
        check(
            "the runtime role and the superuser see the same claim identities",
            super_ids == runtime_ids and len(super_ids) > 0,
            f"superuser={len(super_ids)} runtime={len(runtime_ids)} "
            f"only_superuser={sorted(set(super_ids) - set(runtime_ids))[:2]} "
            f"only_runtime={sorted(set(runtime_ids) - set(super_ids))[:2]}",
        )
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()
        # Leave the database as it was found.
        #
        # This script creates a scratch corpus and a credential. `assert_upgrade.py`
        # runs next on the same database and asserts the corpus list is UNCHANGED
        # across the upgrade -- so an un-removed scratch corpus makes that
        # assertion fail on an otherwise perfect upgrade, which is exactly what it
        # did on CI. A gate that breaks the gate beside it is worse than no gate.
        #
        # In a `finally`, so the cleanup happens on the failure path too: the run
        # that most needs a readable next step is the one that just failed.
        if credential_id:
            _cleanup(superuser, credential_id, args.issued_with)

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("all runtime-role assertions passed")
    return 0


def _cleanup(superuser: dict, credential_id: str, scratch: str) -> None:
    """Revoke the credential and delete the scratch corpus.

    Best effort by design: a teardown that raises would mask the assertion result
    that actually matters. But it prints what it could not do, because a silent
    cleanup failure is how the scratch corpus reached `assert_upgrade.py` in the
    first place.
    """
    for argv, label in (
        (["credential", "revoke", credential_id], "revoke the credential"),
        (["corpus", "delete", scratch, "--yes"], "delete the scratch corpus"),
    ):
        code, output = run([sys.executable, "-m", "cli.main", *argv], superuser)
        if code != 0:
            print(f"  note: could not {label}: {output.strip().splitlines()[-1:]}".strip())


def _tail(path: str, lines: int = 12) -> str:
    """The last few lines of a file, indented, for a failure message."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read().strip().splitlines()[-lines:]
    except OSError:
        return "(no output captured)"
    return "\n".join(f"          {line}" for line in text) or "(no output captured)"


def _wait_for(base: str, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, _ = _status(f"{base}/api/corpora")
        if status is not None:
            return True
        time.sleep(1.0)
    return False


def _field(output: str, key: str) -> str:
    """One value out of `credential issue`'s human-readable output.

    The CLI prints the secret and the id in a prose block rather than as JSON,
    because a secret on stdout should look like a secret. Parsing it by label is
    the only way to get both from one call -- `--quiet` prints the secret alone.
    """
    if key == "id":
        # `credential issued: <name> (<id>)` -- the id is parenthesised on the
        # headline, not on a labelled line of its own.
        for line in output.splitlines():
            if line.strip().startswith("credential issued:"):
                match = re.search(r"\(([^)]+)\)", line)
                if match:
                    return match.group(1).strip()
        return ""
    for line in output.splitlines():
        if line.strip().startswith(f"{key}:"):
            return line.strip().split(":", 1)[1].strip()
    return ""


def _status(url: str) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - "not up yet" is the only question asked
        return None, ""


def _corpora(base: str, credential: str) -> list[str]:
    """Corpus names from `GET /corpora` with this credential.

    Returns a one-element marker rather than `[]` on failure. `[]` is a
    legitimate answer -- a credential whose grants were revoked sees nothing --
    and collapsing the two would let a broken reader report "no leak" instead of
    "the read never happened".
    """
    request = urllib.request.Request(f"{base}/api/corpora")
    request.add_header("Authorization", f"Bearer {credential}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return [f"<http {exc.code}>"]
    except Exception as exc:  # noqa: BLE001
        return [f"<error {type(exc).__name__}>"]
    return sorted(str(row.get("name")) for row in rows if isinstance(row, dict))


def _scoped_claim_ids(credential: str) -> list[str]:
    """Document identities as the runtime role, under the credential's scope.

    In-process, and deliberately so. The point is that the row-level policy
    decides the answer: the principal resolved from this credential carries
    exactly its granted corpus ids, and every transaction opens with that scope
    written to `mindpalace.tenant_scope`.
    """
    import asyncio

    from sqlalchemy import text

    from api.services import authz, db as db_module

    async def read() -> list[str]:
        # TWO transactions, in this order, and the order is the whole point.
        #
        # `after_begin` writes `mindpalace.tenant_scope` when a transaction
        # OPENS. Entering `principal_scope` inside a session that has already
        # run a query therefore changes nothing -- the scope was already written
        # from the previous principal, and the policy filters on that. Resolving
        # the identity in one session and reading in another, with the scope
        # entered before the first read, is how the server does it, and it is the
        # only ordering in which the policy sees this credential's corpora.
        async with db_module.session_scope() as db:
            principal = await authz.resolve_principal(db, f"Bearer {credential}")

        async with db_module.session_scope() as db:
            with db_module.principal_scope(principal):
                rows = (
                    await db.execute(text("SELECT path, id FROM memory_documents ORDER BY path"))
                ).all()
                return sorted(f"{row[0]}={row[1]}" for row in rows)

    return asyncio.run(read())


def _superuser_claim_ids(url: str) -> list[str]:
    """The same read, as a superuser, in a separate process.

    A subprocess because the engine is built from `DATABASE_URL` at import time.
    """
    code, output = run(
        [
            sys.executable,
            "-c",
            "import asyncio\n"
            "from sqlalchemy import text\n"
            "from api.services.db import session_scope\n"
            "async def main():\n"
            "    async with session_scope() as db:\n"
            "        rows=(await db.execute(text("
            "'SELECT path, id FROM memory_documents ORDER BY path'))).all()\n"
            "        [print(f'{a}={b}') for a,b in rows]\n"
            "asyncio.run(main())\n",
        ],
        {"DATABASE_URL": url, "MIND_PALACE_AUTH_MODE": "off", "MIND_PALACE_DEPLOYMENT": "local"},
    )
    if code != 0:
        return []
    return sorted(line.strip() for line in output.splitlines() if "=" in line)


if __name__ == "__main__":
    raise SystemExit(main())
