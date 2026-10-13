"""The wheel must contain every module the package actually imports.

## The bug this exists for

`mindpalace_errors.py` was added to the repository and imported by
`mindpalace_sdk.py`, `api/main.py` and the CLI. It was never added to
`pyproject.toml`'s `py-modules` list.

Nothing failed. The test suite runs from the repository root, where the current
working directory is on `sys.path`, so `mindpalace_errors` resolves to the file in
the working tree whether or not it ships. `check_versioning.py` and
`check_evidence.py` do not import it. Every check passed.

A developer who followed the README got this instead:

```
$ mindpalace init
Traceback (most recent call last):
  File "/work/.venv/bin/mindpalace", line 3, in <module>
    from cli.main import app
  ...
ModuleNotFoundError: No module named 'mindpalace_errors'
```

A built wheel contained seven top-level modules and not this one, so the failure
was in the released artifact and not in the repository.

The reason it survived is that "importable from the source tree" and "declared to
ship" are different facts and only the first one was being checked.

## What is asserted

1. Every top-level `.py` file the package imports by module name is either in
   `py-modules` or inside a discovered package.
2. Building a wheel puts each of those modules in the archive.

The second is slower and is skipped unless `MIND_PALACE_TEST_BUILD_WHEEL=1`, so
the fast check runs on every suite run and the slow one is opt-in.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_DIRS = ("api", "cli")


def _declared() -> tuple[set[str], set[str]]:
    """(top-level modules py-modules declares, discovered package names)."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
    modules = set(config.get("py-modules", []))
    include = config.get("packages", {}).get("find", {}).get("include", [])
    packages = {entry.rstrip("*") for entry in include}
    return modules, packages


def _imported_top_level() -> set[str]:
    """Module names imported by shipped code that resolve to a repo-root file.

    Walks the shipped tree rather than grepping, so a dynamic import inside a
    function is found the same way a top-level one is. Anything whose name is not
    a file in the repository root -- a stdlib or third-party module -- is skipped.
    """
    root_files = {p.stem for p in ROOT.glob("*.py")}
    found: set[str] = set()
    targets = [p for p in ROOT.glob("*.py")] + [
        p for directory in PACKAGED_DIRS for p in (ROOT / directory).rglob("*.py")
    ]
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - would fail the suite elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in root_files:
                        found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in root_files:
                    found.add(node.module.split(".")[0])
    return found


def test_every_imported_top_level_module_is_declared_to_ship():
    declared, _ = _declared()
    missing = sorted(_imported_top_level() - declared)
    assert not missing, (
        "these modules are imported by shipped code but are absent from "
        f"pyproject.toml's py-modules, so they are missing from the wheel: {missing}. "
        "A developer following the README would get ModuleNotFoundError."
    )


def test_the_declared_modules_all_exist():
    declared, _ = _declared()
    absent = sorted(name for name in declared if not (ROOT / f"{name}.py").exists())
    assert not absent, f"py-modules declares files that do not exist: {absent}"


@pytest.mark.skipif(
    os.getenv("MIND_PALACE_TEST_BUILD_WHEEL") != "1",
    reason="set MIND_PALACE_TEST_BUILD_WHEEL=1 to build and inspect a wheel",
)
def test_a_built_wheel_contains_every_declared_module(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "-o", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"wheel build failed:\n{result.stderr[-3000:]}"

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    declared, _ = _declared()
    with zipfile.ZipFile(wheels[0]) as archive:
        shipped = {
            name.split("/")[-1][: -len(".py")]
            for name in archive.namelist()
            if "/" not in name and name.endswith(".py")
        }
    absent = sorted(declared - shipped)
    assert not absent, f"declared in py-modules but absent from the built wheel: {absent}"
