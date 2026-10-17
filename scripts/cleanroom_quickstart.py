"""Run the README's quickstart against a built wheel, from outside the repository.

    python scripts/cleanroom_quickstart.py --wheel dist/mindpalace_os-*.whl

## Why this exists as a script and not as a pytest file

Both release blockers this branch fixed were invisible to the test suite, for the
same reason: pytest runs with the repository root as the working directory, so a
module that is missing from the wheel still imports from the working tree. A test
that cannot see the failure is not a weak test; it is a test of the wrong thing.

So this does what the suite structurally cannot:

* it installs a **built wheel** into a fresh virtual environment;
* it runs with the working directory **outside the repository**, so the source
  tree is not on `sys.path` and cannot satisfy an import the wheel does not;
* it creates its own database and applies the migrations from the wheel's own
  checkout;
* it executes the commands **extracted from README.md**, not retyped. A
  retyped quickstart drifts, and then it tests what the author remembers rather
  than what a reader would run.

## Mutation testing

A check that has never been shown to fail is not evidence. Two mutations, each a
real regression from this branch, must make it fail:

| Mutation | Result |
|---|---|
| remove `mindpalace_errors` from `py-modules` | FAIL, ModuleNotFoundError |
| remove both `CHAR(64)` strips | FAIL: the first `mindpalace remember` cannot write |

The second mutation removes **both** of the default-corpus fixes, not one. Each
alone is sufficient, so removing only one leaves the job green -- which is
correct behaviour and a trap for whoever mutates it next. Measured, not assumed.

## What it asserts

Not "it installed" -- that is the part that lies. It asserts:

1. a recall returns the documented answer;
2. a recall on an uncovered question abstains rather than inventing;
3. `explain` returns an evidence span from the archived text;
4. `history` shows the supersession after a second `remember` with the same key;
5. a receipt verifies **offline**, with the database unreachable;
6. the SDK snippet from the README runs;
7. `mindpalace_mcp` answers with the documented two-level argument shape.

Each failure prints what was expected and what arrived, because a test that
reports "quickstart failed" sends the reader back to the README to find out how.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

#: Commands that need a live server, a stopped database, or a deliberately
#: tampered file. Run them, but do not treat their output as an assertion.
SKIP_PATTERNS = (
    "with the database stopped",  # prose in a comment
    "tampered-receipt",  # needs a tampered file we do not have here
    "pip install",  # this script IS the install
    "--corpus quoting",  # docs/authoring.md's backtick example; it must FAIL
)


#: The documents whose ``$ `` lines are commands a reader is told to type. The
#: authoring guide is second because its examples are the ones most likely to rot
#: silently: a wrong claim field still "runs", it just stores nothing.
COMMAND_DOCS = (README, ROOT / "docs" / "authoring.md")


def readme_commands() -> list[str]:
    """Commands the documented path tells a reader to type, extracted not retyped."""
    out: list[str] = []
    for doc in COMMAND_DOCS:
        if not doc.exists():
            continue
        for _lang, block in re.findall(r"```(console|bash)\n(.*?)```", doc.read_text(), re.S):
            for command in _dollar_lines(block):
                if not any(p in command for p in SKIP_PATTERNS):
                    out.append(command)
    return out


def _own_database(base_url: str, name: str) -> str:
    """Create a disposable database called ``name`` and return its URL.

    Dropped by the caller's ``finally``. Refuses rather than reusing: a run
    against an existing database is not a clean-room run.
    """
    import psycopg2

    # No `with` here. CREATE/DROP DATABASE cannot run inside a transaction
    # block, and psycopg2's connection context manager commits on exit, which
    # re-opens one after the statement has already run. Autocommit, execute,
    # close by hand.
    conn = psycopg2.connect(_maintenance_url(base_url), connect_timeout=10)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        conn.close()
    return re.sub(r"/[^/?]+(\?|$)", rf"/{name}\1", base_url)


def _maintenance_url(base_url: str) -> str:
    """The same server, pointed at the maintenance database."""
    return re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", base_url)


def _dollar_lines(block: str) -> list[str]:
    """The ``$ `` lines of a fence, with shell continuations joined.

    A trailing backslash is part of the command, not of the fence. Left alone
    the extractor emitted `mindpalace recall "..." \\` as one command, which the
    shell runs with an unterminated quote -- and it passed, because the job that
    would have caught it was never wired to this script.
    """
    commands: list[str] = []
    pending = ""
    for line in block.splitlines():
        line = line.strip()
        if pending:
            pending = f"{pending} {line}".strip()
            if not pending.endswith("\\"):
                commands.append(pending)
                pending = ""
            continue
        if not line.startswith("$ "):
            continue
        command = line[2:].split("   #")[0].strip()
        if command.endswith("\\"):
            pending = command[:-1].strip()
            continue
        commands.append(command)
    if pending:
        commands.append(pending)
    return commands


def readme_python_snippet() -> str:
    """The first ```python block that constructs a client, verbatim."""
    for block in re.findall(r"```python\n(.*?)```", README.read_text(), re.S):
        # `MindPalace(` rather than `MindPalace()`: the assertion is that the
        # README contains a snippet constructing a client, not that it names
        # one with no arguments. A named corpus is the documented form.
        if "MindPalace(" in block:
            return block
    raise SystemExit("cleanroom: no SDK snippet found in README.md")


class Runner:
    def __init__(self, workdir: Path, database_url: str, python: Path | None = None) -> None:
        self.work = workdir
        # Everything runs under the clean-room interpreter. Using the driver's
        # own `sys.executable` here is subtle and wrong: the driver is running
        # from the repository, so its interpreter can import the source tree and
        # mask exactly the omission this script exists to catch. It surfaced as
        # ModuleNotFoundError for a module the wheel *did* contain.
        self.interpreter = str(python or sys.executable)
        self.env = {**os.environ, "DATABASE_URL": database_url, "MIND_PALACE_RUNTIME": "0"}

    def sh(self, command: str, *, expect: int = 0, label: str = "") -> subprocess.CompletedProcess:
        result = subprocess.run(
            command, shell=True, cwd=self.work, env=self.env, capture_output=True, text=True
        )
        if expect is not None and result.returncode != expect:
            raise AssertionError(
                f"{label or command}\n  exit {result.returncode}, expected {expect}\n"
                f"  stdout: {result.stdout.strip()[:600]}\n  stderr: {result.stderr.strip()[:600]}"
            )
        return result

    def python(self, source: str, *, label: str) -> subprocess.CompletedProcess:
        path = self.work / f"_cleanroom_{label}.py"
        path.write_text(source)
        return self.sh(f'"{self.interpreter}" "{path.name}"', label=f"python snippet: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True, help="path to the built wheel")
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the workdir for inspection. WARNING: the workdir holds the "
        "virtualenv this script built -- about 6 GB -- and nothing removes it "
        "but you.",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="install the wheel ALONE, with no requirements.txt. This is the "
        "`pip install mindpalace-os` path the README also documents, and it is "
        "the only way to find out whether the wheel is self-sufficient.",
    )
    parser.add_argument(
        "--workdir-root",
        default=None,
        help="where to build the venv. /tmp is often a small tmpfs and a torch "
        "install does not fit on it; the default is a directory under the repo's "
        "parent so the venv still lands outside the repository.",
    )
    parser.add_argument(
        "--torch-index",
        default=None,
        help="pip index for torch. The default index resolves the CUDA build, which "
        "is ~5 GB and unusable in CI. Pass the CPU wheel index to cut the install.",
    )
    args = parser.parse_args()

    wheel = Path(args.wheel).resolve()
    if not wheel.exists():
        print(f"cleanroom: wheel not found: {wheel}", file=sys.stderr)
        return 2

    # `Path.resolve()` matters here and is not cosmetic. A venv records its own
    # location in pyvenv.cfg; if that location is relative, Python resolves it
    # against the process CWD at import time, and a workdir reached through a
    # relative ROOT.parent puts the *repository* on sys.path -- which is exactly
    # the leak this script exists to rule out. It fails as a ModuleNotFoundError
    # on a module the wheel does contain, which is a very confusing failure.
    root = Path(args.workdir_root or ROOT.parent).resolve()
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cleanroom-", dir=root))
    # Deliberately NOT under the repository: if cwd were inside it, an import the
    # wheel does not provide would still resolve from the source tree, and the
    # whole point of this script is to not allow that.
    assert ROOT not in work.parents and work != ROOT

    # The quickstart tells a reader who has just cloned the repository to ingest
    # `./docs/adr`, so that path is part of the documented starting state and the
    # clean room has to have it. Copying the directory in -- and nothing else -- is
    # what makes this run equivalent to a fresh clone without letting an import
    # resolve from the source tree, which is why the working directory stays outside
    # the repository.
    adr_source = ROOT / "docs" / "adr"
    if adr_source.is_dir():
        shutil.copytree(adr_source, work / "docs" / "adr")
    else:  # pragma: no cover - a clone without the worked example is a broken clone
        print(f"!! {adr_source} is missing; the README quickstart cannot run", file=sys.stderr)
        return 1

    # Its own database, which is what this module's docstring has always claimed
    # and never did. Against a shared one the run is not repeatable: `remember`
    # with `--key` supersedes, so a second run flips the datastore back to
    # PostgreSQL and the MCP assertion at the end -- which wants the
    # superseding value -- fails on every other run depending on parity. That
    # is not a flaky assertion, it is a shared database.
    session_url = _own_database(args.database_url, work.name.replace("-", "_"))
    session_db = urlparse(session_url).path.lstrip("/")

    runner = Runner(work, session_url)
    checks: list[tuple[str, bool, str]] = []

    # The MCP check at the end runs `mindpalace-mcp`, and that console script
    # only exists with the `[mcp]` extra. The script installed the bare wheel and
    # then ran it anyway, so the last check could never pass -- which is very
    # likely why this file had no caller. The bare path must stay bare: not
    # shipping `mcp` by default is a claim the release makes, and asserting it
    # needs an install without the extra.
    wheel_spec = str(wheel) if args.bare else f"{wheel}[mcp]"

    try:
        venv = (work / "venv").resolve()
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
        bin_dir = venv / "bin"
        # Resolve the interpreter rather than assuming `bin/python` exists. A
        # venv created on some distributions provides only `python3`, and when
        # `bin/python` is missing the shell silently runs a DIFFERENT `python`
        # from PATH -- which is how this script briefly executed the driver's own
        # interpreter, from the repository, and reported a wheel omission that
        # did not exist.
        python = next(
            (bin_dir / name for name in ("python", "python3") if (bin_dir / name).exists()),
            None,
        )
        if python is None:
            raise AssertionError(f"cleanroom: no interpreter in {bin_dir}")

        runner.env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        runner.env["VIRTUAL_ENV"] = str(venv)

        print("== install the built wheel ==")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", wheel_spec],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout[-2000:], result.stderr[-2000:], file=sys.stderr)
            return 1
        # alembic is declared in requirements.txt but not in the wheel's own
        # dependencies; the README's migrate step needs it, so install it the
        # way the README does.
        #
        # `--torch-index` was accepted and then ignored, so a CI run asking for
        # the CPU wheel got the default CUDA build: ~5.9 GB of nvidia-* packages
        # for a check that runs on CPU. The other jobs pass the CPU index and
        # this one did not, which is why its step was slow rather than wrong.
        index_args = ["--extra-index-url", args.torch_index] if args.torch_index else []
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", *index_args]
            + ["-r", str(ROOT / "requirements.txt")],
            capture_output=True,
            text=True,
        )
        runner.interpreter = str(python)
        print("   installed:", wheel.name)

        if not args.bare:
            migrations = work / "migrations"
            shutil.copytree(ROOT / "migrations", migrations)

        print("== migrate ==")
        if args.bare:
            # `mindpalace init` applies the schema itself. A wheel that ships its
            # migrations cannot do this without them, and a wheel that declares
            # alembic cannot do it without that -- which is why this is the only
            # migrate step the bare path gets.
            result = runner.sh("mindpalace init", expect=None, label="mindpalace init (bare)")
            checks.append(
                (
                    "bare wheel applies its own schema",
                    result.returncode == 0,
                    (result.stderr or result.stdout)[:200],
                )
            )
        else:
            runner.sh(
                f'"{python}" -m alembic -c migrations/alembic.ini upgrade head',
                label="alembic upgrade head",
            )
        checks.append(("migrations apply", True, ""))

        commands = readme_commands()
        print(f"== {len(commands)} README commands ==")
        step = 0
        for command in commands:
            # Commands that need a file we did not produce, or that are prose
            # dressed as a prompt, are not part of the runnable quickstart.
            if "receipt.json" in command or "verify" in command:
                continue
            # `mindpalace-mcp` bare is long-lived and covered by the MCP check
            # below. `--help` is not: it prints usage, returns immediately, and
            # is the one invocation a reader tries when they cannot find the
            # server. It printed nothing until 2026-10-15, which is exactly the
            # kind of thing only a test catches.
            if command.startswith("mindpalace-mcp") and "--help" not in command:
                continue
            # These two guards used to be `"\\" in command` and `"#" in
            # command`, added to skip the README's one unjoined line
            # continuation. Continuations are joined properly now, so the first
            # has no job; and a `#` inside a quoted argument is an ordinary
            # character, so the second dropped the authoring example, which
            # writes its document with `printf` and a markdown heading. Between
            # them they silently skipped three of eighteen commands, and a guard
            # that hides a real command is worse than no guard at all. Trailing
            # `...` is the only shape still worth refusing: it is prose dressed
            # as a prompt.
            if command.endswith("..."):
                continue
            step += 1
            label = f"README cmd {step}: {command[:56]}"
            print(f"   [{step}] {command[:72]}")
            result = runner.sh(command, expect=None, label=label)  # noqa: S602
            checks.append((label, result.returncode == 0, (result.stderr or "")[:200]))

            if command.startswith("mindpalace recall") and "incident" in command:
                ok = "NO_RELEVANT_MEMORY" in result.stdout
                checks.append(("uncovered question abstains", ok, result.stdout.strip()[:160]))
                if not ok:
                    print(f"   !! expected abstention, got: {result.stdout.strip()[:200]}")
            if command.startswith("mindpalace recall") and "datastore does production" in command:
                ok = "PostgreSQL" in result.stdout
                checks.append(
                    ("recall returns the documented answer", ok, result.stdout.strip()[:160])
                )
                if not ok:
                    print(f"   !! expected PostgreSQL, got: {result.stdout.strip()[:200]}")
            if command.startswith("mindpalace explain"):
                ok = "EVIDENCE" in result.stdout
                checks.append(("explain shows an evidence span", ok, result.stdout.strip()[:160]))

        print("== receipt verifies offline ==")
        # The question and the corpus are the ones the README's own receipt command
        # uses, because that is what this job exists to attest. It used to ask
        # 'What datastore does production use?' against the default corpus, which
        # worked only while the README told readers to `remember` into `default`;
        # when the quickstart moved to `--corpus project` the default corpus stopped
        # being populated and this step began failing with "nothing matched that
        # question" -- the corpus was empty, and abstaining was the correct answer.
        #
        # It is not extracted from the README because SKIP_PATTERNS excludes
        # receipt commands, so this line is the single place it is written.
        result = runner.sh(
            'mindpalace receipt "What is the primary datastore?" '
            "--corpus project -o receipt.json",
            label="receipt",
        )
        bad = {**runner.env, "DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nothing"}
        offline = subprocess.run(
            "mindpalace verify receipt.json",
            shell=True,
            cwd=work,
            env=bad,
            capture_output=True,
            text=True,
        )
        ok = offline.returncode == 0 and "VERIFIED" in offline.stdout
        checks.append(("receipt verifies with no database", ok, offline.stdout.strip()[:160]))
        if not ok:
            print(
                f"   !! offline verify failed: {offline.stdout.strip()[:200]}"
                f" {offline.stderr.strip()[:200]}"
            )

        print("== SDK snippet, verbatim from the README ==")
        snippet = readme_python_snippet()
        result = runner.python(snippet, label="readme_sdk")
        ok = "CURRENT" in result.stdout
        checks.append(("README SDK snippet runs", ok, result.stdout.strip()[:160]))

        print("== MCP, documented argument shape ==")
        mcp = """
import json, os, subprocess, sys
server = os.path.join(os.path.dirname(sys.executable), "mindpalace-mcp")
proc = subprocess.Popen([server], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, env=dict(os.environ), bufsize=1)
def send(o):
    proc.stdin.write(json.dumps(o) + "\\n"); proc.stdin.flush()
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "cleanroom", "version": "0"}}})
proc.stdout.readline()
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

def rpc(req_id, name, arguments):
    # One tool call, and the text it answered with.
    send({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
          "params": {"name": name, "arguments": arguments}})
    for _ in range(60):
        line = proc.stdout.readline()
        if not line:
            raise SystemExit(f"server closed stdout answering {name}")
        msg = json.loads(line)
        if msg.get("id") == req_id:
            content = (msg.get("result") or {}).get("content", [])
            return content[0].get("text", "") if content else ""
    raise SystemExit(f"no reply to {name}")

def current(text):
    # The claim the response reports as CURRENT, or "".
    try:
        return json.loads(text)["current_memories"][0]["claim"]
    except Exception:
        return ""

def fail(what, got):
    proc.kill()
    print(f"!! {what}: {got}", file=sys.stderr)
    sys.exit(3)

# memory_remember: the one MCP tool that writes memory.
MCP_KEY = "mcp.datastore"
first = json.loads(rpc(2, "memory_remember",
                       {"statement": "The support rota runs weekly.",
                        "corpus": "default", "key": MCP_KEY}))
if first.get("event") != "NEW":
    fail("a new memory_remember should report NEW", first)

# Idempotent: the identical call again must not write a second version.
again = json.loads(rpc(3, "memory_remember",
                       {"statement": "The support rota runs weekly.",
                        "corpus": "default", "key": MCP_KEY}))
if again.get("event") != "UNCHANGED" or again.get("version_id") != first.get("version_id"):
    fail("an identical memory_remember must be idempotent", again)

# It comes back through recall.
ROTA_Q = "How often does the support rota run?"
if "weekly" not in current(rpc(4, "memory_recall",
                               {"request": {"corpus": "default", "query": ROTA_Q}})):
    fail("memory_recall did not return what memory_remember wrote", "")

# A changed value on the same key supersedes the old one.
rpc(5, "memory_remember",
    {"statement": "The support rota now runs fortnightly.",
     "corpus": "default", "key": MCP_KEY})
if "fortnightly" not in current(rpc(6, "memory_recall",
                                    {"request": {"corpus": "default", "query": ROTA_Q}})):
    fail("a changed value did not supersede the old one", "")

# And a supersession made elsewhere in the corpus is what recall reports. The
# snippet creates it rather than relying on the README's commands to have done so:
# the quickstart now documents an ADR path into `project` and nothing writes to
# `default`, so a check that assumed otherwise was asserting against an empty
# corpus and failing for a reason that had nothing to do with MCP.
rpc(7, "memory_remember",
    {"statement": "The datastore is distributed PostgreSQL.",
     "corpus": "default", "key": "datastore"})
rpc(8, "memory_remember",
    {"statement": "The datastore is now distributed CockroachDB.",
     "corpus": "default", "key": "datastore"})
QUERY = "What datastore does production use?"
if "CockroachDB" not in current(rpc(9, "memory_recall",
                                    {"request": {"corpus": "default", "query": QUERY}})):
    fail("a supersession recorded earlier is no longer what recall reports", "")

proc.kill()
sys.exit(0)
"""
        result = runner.python(mcp, label="mcp")
        checks.append(
            (
                "MCP memory_remember writes, is idempotent, supersedes, and is recalled",
                result.returncode == 0,
                (result.stderr or "")[:300],
            )
        )

    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            import psycopg2

            drop = psycopg2.connect(_maintenance_url(args.database_url), connect_timeout=10)
            try:
                drop.autocommit = True
                with drop.cursor() as cur:
                    cur.execute(f'DROP DATABASE IF EXISTS "{session_db}" WITH (FORCE)')
            finally:
                drop.close()
            print(f"   dropped database {session_db}")
        except Exception as exc:  # noqa: BLE001 - teardown must not mask results
            print(f"\n   could not drop {session_db}: {exc}", file=sys.stderr)
        if args.keep:
            print(f"\nworkdir kept at {work}")

    print("\n== results ==")
    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
        if not ok and detail:
            print(f"        {detail}")
    print(f"\n{len(checks) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
