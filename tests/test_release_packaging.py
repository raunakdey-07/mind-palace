# SPDX-License-Identifier: Apache-2.0
"""The distribution must ship the runtime, and not the research tooling.

`scripts/` was in the wheel's `include` list from `v0.6.0`, which meant every
`pip install mindpalace-os` carried the release checks, the benchmark harnesses and
the scale fixtures. Measured: 54 files, 590 KB of 1,328 KB unpacked -- 44% of the
distribution -- for code that no runtime path imports.

Three tests here. They are cheap and they need no model and no database, so they run
in the same instant as the rest of the suite and fail loudly if the boundary is
reopened by accident.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _config() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_the_wheel_does_not_ship_research_tooling():
    """`scripts*` must not come back.

    Regression guard with a reason: it was there for seven releases and nobody
    noticed because it only ever showed up as a slightly larger install.
    """
    include = _config()["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "scripts*" not in include, (
        f"scripts* is back in the wheel include list {include}. It ships release and "
        "benchmark tooling no runtime imports, which measured at 44% of the "
        "unpacked distribution."
    )


def test_the_runtime_surface_is_still_included():
    """Removing bloat must not remove the product.

    Both halves matter: the packages, and the single-file modules the console
    scripts and the local runtime entry point need. `mindpalace_runtime` going
    missing is the failure that once made every command silently 6 seconds on an
    installed package.
    """
    config = _config()
    include = set(config["tool"]["setuptools"]["packages"]["find"]["include"])
    modules = set(config["tool"]["setuptools"]["py-modules"])

    assert {"api*", "cli*"} <= include, include
    for required in (
        "mindpalace_sdk",
        "mindpalace_runtime",
        "memory_receipt",
        "memory_proof",
        "memory_proof_cli",
        "memory_pack",
        "mcp_server",
    ):
        assert required in modules, f"{required} is no longer a py-module; the wheel loses it"

    scripts = config["project"]["scripts"]
    assert scripts["mindpalace"] == "cli.main:app", scripts
    assert scripts["mindpalace-proof"] == "memory_proof_cli:main", scripts


def test_nothing_outside_scripts_imports_the_scripts_package():
    """The reason the exclusion is safe, asserted rather than assumed.

    Three test modules import `scripts.benchmark` to check the frozen benchmark and
    the release workflow -- they run from a checkout, where `scripts/` is present,
    which is fine. Nothing that ships in the wheel may do the same, because the
    wheel no longer contains it.
    """
    shipped = [ROOT / "api", ROOT / "cli"]
    shipped += [
        ROOT / f"{name}.py"
        for name in (
            "mindpalace_sdk",
            "mcp_server",
            "memory_pack",
            "memory_proof",
            "memory_proof_cli",
            "memory_receipt",
        )
    ]

    offenders: list[str] = []
    for path in shipped:
        files = path.rglob("*.py") if path.is_dir() else [path]
        for file in files:
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders += [
                        f"{file.relative_to(ROOT)}:{node.lineno} import {a.name}"
                        for a in node.names
                        if a.name == "scripts" or a.name.startswith("scripts.")
                    ]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "scripts" or module.startswith("scripts."):
                        offenders.append(f"{file.relative_to(ROOT)}:{node.lineno} from {module}")
    assert not offenders, (
        "shipped code imports the scripts package, which the wheel no longer "
        f"contains: {offenders}"
    )
