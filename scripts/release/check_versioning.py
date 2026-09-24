"""Check milestone-to-release metadata used by CI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(text: str, pattern: str, label: str) -> None:
    if not re.search(pattern, text, re.MULTILINE):
        raise SystemExit(f"release metadata check failed: {label}")


def main() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_map = (ROOT / "docs/release-map.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m006 = (ROOT / "docs/evaluation/m006.md").read_text(encoding="utf-8")
    m0065 = (ROOT / "docs/evaluation/m0065.md").read_text(encoding="utf-8")
    m00675 = (ROOT / "docs/evaluation/m00675-reproducibility.md").read_text(encoding="utf-8")

    require(pyproject, r'^version = "0\.6\.0"$', "package version is v0.6.0")
    require(changelog, r"^## \[v0\.6\.0\]", "v0.6.0 changelog entry")
    require(release_map, r"\| M006\.75 \|.*\| `v0\.5\.0` \|", "M006.75 release mapping")
    require(
        readme,
        r"\[Current release: v0\.6\.0\]\(docs/release-map\.md\)",
        "README release-map link",
    )
    require(m006, r"^Public release: `v0\.5\.0`$", "M006 release metadata")
    require(m0065, r"^Public release: `v0\.5\.0`$", "M006.5 release metadata")
    require(m00675, r"^Public release: `v0\.5\.0`$", "M006.75 release metadata")
    require(
        release_map,
        r"\| M007 / M007\.1 \|.*\| `v0\.5\.1` \|",
        "M007.1 handoff release mapping",
    )
    require(
        release_map,
        r"\| M009 \|.*\| `v0\.6\.0` \|",
        "M009 release mapping",
    )
    print("release metadata: PASS")


if __name__ == "__main__":
    main()
