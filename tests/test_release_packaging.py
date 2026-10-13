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


def test_each_console_script_declares_the_extra_it_needs():
    """A console script that cannot run from its declared install is a packaging bug.

    This is not hypothetical. The wheel CI job asserted `mindpalace-mcp --help`
    against a **bare** install, which cannot supply `mcp` -- it is a declared extra.
    The job failed with `ModuleNotFoundError: No module named 'mcp'`, and its
    `|| import mcp_server` fallback could not have saved it, for the same reason.

    `typer` is the interesting counter-case, and the reason this test is worth having.
    It is also an extra, so `mindpalace` ought to be uninstallable without it -- but a
    bare install *does* get typer, because `transformers` requires it. So the CLI's
    availability depends on an unrelated package's dependency graph, and this test
    pins the declaration rather than the accident.

    Every dependency a console script's module needs must therefore be a base
    dependency or reachable through an extra. The mapping is stated here rather than
    remembered, so adding a console script, or moving an import, has to be deliberate.
    """
    config = _config()
    base = {_requirement_name(d) for d in config["project"]["dependencies"]}
    extras = {
        name: {_requirement_name(d) for d in reqs}
        for name, reqs in config["project"].get("optional-dependencies", {}).items()
    }

    expectations = {
        # console script -> (what it needs, the extra that provides it)
        "mindpalace": ({"typer"}, "cli"),
        "mindpalace-proof": (set(), None),
        "mindpalace-mcp": ({"mcp"}, "mcp"),
    }

    scripts = config["project"]["scripts"]
    assert set(scripts) == set(
        expectations
    ), f"console scripts changed to {sorted(scripts)}; update this mapping deliberately"

    for script, (needed, extra) in expectations.items():
        available = set(base) | (extras.get(extra, set()) if extra else set())
        missing = needed - available
        assert not missing, (
            f"`{script}` needs {sorted(missing)}, which a "
            f"'mindpalace-os{'[' + extra + ']' if extra else ''}' install does not "
            f"provide. Base: {sorted(base)}; {extra!r} extra: "
            f"{sorted(extras.get(extra, [])) if extra else 'nothing extra'}."
        )

    # The portability claim rests on this one staying dependency-free: someone handed
    # a receipt may install the wheel and nothing else.
    assert not expectations["mindpalace-proof"][0] & base, (
        "mindpalace-proof gained a base dependency; verifying a receipt must need no "
        "database, model, server or network"
    )


def _requirement_name(requirement: str) -> str:
    """`sqlalchemy[asyncio]>=2.0.0` -> `sqlalchemy`."""
    name = requirement.split(";")[0].strip()
    for separator in (">=", "==", "~=", "<", ">", "[", "(", " "):
        name = name.split(separator)[0]
    return name.strip().lower()
