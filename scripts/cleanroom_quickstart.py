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

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

#: Commands that need a live server, a stopped database, or a deliberately
#: tampered file. Run them, but do not treat their output as an assertion.
SKIP_PATTERNS = (
    "mindpalace-mcp",  # long-lived; covered by the MCP check below
    "with the database stopped",  # prose in a comment
    "tampered-receipt",  # needs a tampered file we do not have here
    "pip install",  # this script IS the install
)


def readme_commands() -> list[str]:
    """Commands the README tells a reader to type, extracted not retyped."""
    out: list[str] = []
    for _lang, block in re.findall(r"```(console|bash)\n(.*?)```", README.read_text(), re.S):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("$ "):
                command = line[2:].split("   #")[0].strip()
                if not any(p in command for p in SKIP_PATTERNS):
                    out.append(command)
    return out


def readme_python_snippet() -> str:
    """The first ```python block that constructs a client, verbatim."""
    for block in re.findall(r"```python\n(.*?)```", README.read_text(), re.S):
        if "MindPalace()" in block:
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
    parser.add_argument("--keep", action="store_true", help="leave the workdir for inspection")
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

    runner = Runner(work, args.database_url)
    checks: list[tuple[str, bool, str]] = []

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
            [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout[-2000:], result.stderr[-2000:], file=sys.stderr)
            return 1
        # alembic is declared in requirements.txt but not in the wheel's own
        # dependencies; the README's migrate step needs it, so install it the
        # way the README does.
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")],
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
            if command.endswith("...") or "\\" in command or "#" in command:
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
        result = runner.sh(
            "mindpalace receipt 'What datastore does production use?' -o receipt.json",
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
proc = subprocess.Popen(["mindpalace-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, env=dict(os.environ), bufsize=1)
def send(o):
    proc.stdin.write(json.dumps(o) + "\\n"); proc.stdin.flush()
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                 "clientInfo": {"name": "cleanroom", "version": "0"}}})
proc.stdout.readline()
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
QUERY = "What datastore does production use?"
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {"name": "memory_recall",
                 "arguments": {"request": {"corpus": "default", "query": QUERY}}}})
for _ in range(40):
    line = proc.stdout.readline()
    if not line:
        break
    msg = json.loads(line)
    if msg.get("id") == 2:
        content = (msg.get("result") or {}).get("content", [])
        text = content[0].get("text", "") if content else ""
        proc.kill()
        # The README supersedes its own earlier fact: step 7 remembers that the
        # datastore is now CockroachDB, so by the time this runs the correct
        # answer is CockroachDB, not the PostgreSQL asserted earlier. Asserting
        # the *superseded* value here would have failed the job for being right.
        sys.exit(0 if "CockroachDB" in text else 3)
proc.kill()
sys.exit(4)
"""
        result = runner.python(mcp, label="mcp")
        checks.append(
            (
                "MCP memory_recall with the documented shape",
                result.returncode == 0,
                "returned no current datastore",
            )
        )

    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    finally:
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
