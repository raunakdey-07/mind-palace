"""Check milestone-to-release metadata used by CI."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(text: str, pattern: str, label: str) -> None:
    if not re.search(pattern, text, re.MULTILINE):
        raise SystemExit(f"release metadata check failed: {label}")


def require_absent(text: str, pattern: str, label: str) -> None:
    """Fail if something appears that must not.

    For claims about releases that do not exist. A leftover reference is worse than a
    missing one: a user reads it and installs a version that was never published.
    """
    found = re.search(pattern, text, re.MULTILINE)
    if found:
        line = text[: found.start()].count("\n") + 1
        raise SystemExit(
            f"release metadata check failed: {label} " f"(line {line}: {found.group(0)!r})"
        )


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _parse_revision(text: str, path: Path) -> str | None:
    """Parse ``revision = "x"`` or ``revision: type = "x"``. None if unparseable."""
    m = re.search(r'^revision\s*(?::\s*[^=\n]*)?\s*=\s*"([^"]*)"', text, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"^revision\s*(?::\s*[^=\n]*)?\s*=\s*'([^']*)'", text, re.MULTILINE)
    return m.group(1) if m else None


def _parse_down_revisions(text: str, path: Path) -> list[str] | None:
    """Parse down_revision into a list of referenced revisions.

    Handles None, empty tuple/list, single quoted value, and tuple/list of
    quoted values (merge revisions). Returns None when the value is present
    but in a form we cannot parse statically (fail loud). A missing
    down_revision line is alembic-valid (defaults to None) and yields [].
    """
    m = re.search(r"^down_revision\s*(?::\s*[^=\n]*)?\s*=\s*(.+)$", text, re.MULTILINE)
    if m is None:
        return []
    raw = m.group(1).strip()
    if raw in ("None", "none", "()", "[]"):
        return []
    if raw.startswith('"') and raw.endswith('"'):
        return [raw[1:-1]]
    if raw.startswith("'") and raw.endswith("'"):
        return [raw[1:-1]]
    if raw.startswith("(") and raw.endswith(")"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part) for part in inner.split(",")]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part) for part in inner.split(",")]
    return None


def analyze_migration_heads(
    versions_dir: Path,
) -> tuple[str | None, list[str]]:
    """Return (head, problems) for the migrations in versions_dir.

    head is the unique alembic head, or None when it cannot be determined.
    problems names zero heads, multiple heads, and any migration file whose
    revision/down_revision cannot be parsed (fail loud).
    """
    down_revisions: dict[str, list[str]] = {}
    problems: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        text = path.read_text(encoding="utf-8")
        rev = _parse_revision(text, path)
        if rev is None:
            problems.append(f"{path.name}: could not parse revision")
            continue
        downs = _parse_down_revisions(text, path)
        if downs is None:
            problems.append(f"{path.name}: could not parse down_revision")
            continue
        down_revisions[rev] = downs

    if problems:
        return None, problems

    referenced = {d for downs in down_revisions.values() for d in downs}
    heads = [rev for rev in down_revisions if rev not in referenced]
    if len(heads) == 0:
        problems.append("no alembic head found: every migration is referenced as a down_revision")
        return None, problems
    if len(heads) > 1:
        problems.append("multiple alembic heads: " + ", ".join(sorted(heads)))
        return None, problems
    return heads[0], problems


def real_alembic_head(versions_dir: Path | None = None) -> str | None:
    """The real migration head, computed from the migration files themselves.

    A head is a revision that no other migration lists as its down_revision.
    Pure stdlib: no database connection and no alembic import required, so this
    check runs in CI without a Postgres service. ``versions_dir`` defaults to
    the real migrations directory; tests pass a temp dir.
    """
    if versions_dir is None:
        versions_dir = ROOT / "migrations" / "versions"
    head, _problems = analyze_migration_heads(versions_dir)
    return head


def doc_named_migration_head(status_text: str) -> str | None:
    """The migration head named in STATUS.md's release-state table."""
    m = re.search(r"\|\s*migration head\s*\|\s*`([^`]+)`", status_text)
    return m.group(1) if m else None


def migration_head_problems(status_text: str, versions_dir: Path) -> list[str]:
    """Compare the doc-named migration head with the real alembic head.

    Returns a problem list (empty when everything is consistent). Fails loud
    when the head cannot be determined: zero heads, multiple heads (named),
    or an unparseable migration file.
    """
    head, problems = analyze_migration_heads(versions_dir)
    if problems:
        return problems
    doc_head = doc_named_migration_head(status_text)
    if doc_head is None:
        return [f"STATUS.md does not name a migration head; real alembic head is {head}"]
    if doc_head != head:
        return [
            f"STATUS.md names migration head {doc_head!r} but the real alembic head is {head!r}"
        ]
    return []


def main() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_map = (ROOT / "docs/release-map.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m006 = (ROOT / "docs/evaluation/m006.md").read_text(encoding="utf-8")
    m0065 = (ROOT / "docs/evaluation/m0065.md").read_text(encoding="utf-8")
    m00675 = (ROOT / "docs/evaluation/m00675-reproducibility.md").read_text(encoding="utf-8")

    require(pyproject, r'^version = "0\.9\.3"$', "package version is v0.9.3")
    require(changelog, r"^## \[v0\.9\.3\]", "v0.9.3 changelog entry")
    require(changelog, r"^## \[v0\.9\.2\]", "v0.9.2 changelog entry")
    require(changelog, r"^## \[v0\.9\.1\]", "v0.9.1 changelog entry")
    # v0.9.0 shipped M015.2 + M016 and must keep its entry. A patch release may not
    # erase the minor release it follows.
    require(changelog, r"^## \[v0\.9\.0\]", "v0.9.0 changelog entry still present")
    require(
        release_map,
        r"\| M006\.75 \|.*\| `v0\.5\.0` \|",
        "M006.75 release mapping",
    )
    require(
        readme,
        r"\[Current release: v0\.9\.3\]\(docs/release-map\.md\)",
        "README release-map link",
    )
    # v0.10.0 was staged during M016 and then folded into v0.9.0. Nothing may *claim*
    # it exists -- a changelog heading, a release-map row, a package version, or a
    # README link would all tell a user to install a release that was never published.
    # Explaining that it does not exist is fine and is not what this rejects.
    require_absent(pyproject, r'version = "0\.10\.0"', "the package is not v0.10.0")
    require_absent(changelog, r"^## \[v0\.10\.0\]", "there is no v0.10.0 changelog entry")
    require_absent(release_map, r"\| `v0\.10\.0` \|", "no release-map row maps to v0.10.0")
    require_absent(readme, r"Current release: v0\.10\.0", "the README does not point at v0.10.0")
    require_absent(
        release_map,
        r"`v0\.10\.0` is the current",
        "v0.10.0 is not the current release",
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
    require(
        release_map,
        r"\| M010 \|.*\| `v0\.7\.0` \|",
        "M010 release mapping",
    )
    require(
        release_map,
        r"\| M011 \|.*\| `v0\.7\.0` \|",
        "M011 release mapping",
    )
    require(
        release_map,
        r"\| M011\.5 \|.*\| `v0\.7\.0` \|",
        "M011.5 release mapping",
    )
    require(
        release_map,
        r"\| M012 / M012\.3 \|.*\| `v0\.7\.0` \|",
        "M012 release mapping",
    )
    # The trust boundary is part of the public contract, not a comment. A release
    # that ships verification without stating what verification does not establish
    # is worse than one that ships no verification.
    require(
        changelog,
        r"does \*\*not\*\* establish that the original source was factually correct",
        "v0.8.0 trust boundary",
    )
    require(
        release_map,
        r"does not\nestablish that the original source was factually correct",
        "release map trust boundary",
    )
    # v0.9.0 (M015.2 + M016) is the release about a first-time user reaching the
    # product and then keeping it. The README has to open on the commands, and the
    # release map has to say what they are, or the release documents a capability
    # nobody can find.
    #
    # The heading was "The five-minute path" until the Quickstart was rewritten
    # for one concrete reader -- a developer wiring this into their coding agent.
    # What the check is for is unchanged: the README must open on runnable
    # commands, not on a description of the product.
    require(
        readme,
        r"^## Quickstart$",
        "README opens on the Quickstart",
    )
    require(readme, r"mindpalace remember", "README shows the documented command")
    require(readme, r"mindpalace verify", "README shows offline verification")
    require(release_map, r"\| M015\.2\b.*\| `v0\.9\.0` \|", "M015.2 release mapping")

    # M015.2 and M016 are one release, and the map has to say so rather than leaving a
    # reader to infer it from two rows.
    require(release_map, r"\| M016\b.*\| `v0\.9\.0` \|", "M016 release mapping")
    require(
        release_map,
        r"consolidated into a single public release",
        "the map explains the consolidation",
    )

    # Two claims the release makes that must not quietly disappear: the runtime is
    # optional, and the rejected lexical fast path stays rejected and written down --
    # because on 15 of 16 questions it looked safe.
    require(changelog, r"\*\*A lexical fast path\.\*\*", "the rejected fast path is recorded")
    require(
        changelog,
        r"would have made `NO_RELEVANT_MEMORY` depend on",
        "the fast path's reason for rejection",
    )
    require(readme, r"mindpalace runtime status", "README says how to check the runtime")
    require(readme, r"MIND_PALACE_LEXICAL", "README documents the model-free mode")
    require(readme, r"131/157", "README keeps the held-out research figure honest")
    # The frozen benchmark's accuracy and its safety invariants are different
    # numbers. A release must not report one as the other.
    require(changelog, r"45/60", "the changelog states the measured frozen benchmark accuracy")
    require(
        changelog,
        r"60 of 60 identical canonical\s+per-question decisions",
        "the changelog states the measured no-change evidence",
    )
    require(changelog, r"60/60", "the changelog states the safety invariants")
    require(
        changelog,
        r"131/157",
        "the changelog keeps the held-out research figure honest",
    )

    # Both examples must keep working, or the first thing a new user copies breaks.
    require(readme, r"examples/decision-memory/", "README links the decision-memory example")
    require(readme, r"examples/project-log/", "README links the project-log example")

    # The example's input file is data, not prose, and a blanket `*.md` ignore will
    # silently drop it -- leaving a fresh clone with an example that exits immediately.
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "examples/project-log/decisions.md"],
        cwd=ROOT,
        capture_output=True,
    )
    if ignored.returncode == 0:
        raise SystemExit(
            "release metadata check failed: examples/project-log/decisions.md is "
            "gitignored, so the example's data file would not ship"
        )
    status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    versions_dir = ROOT / "migrations" / "versions"
    for problem in migration_head_problems(status, versions_dir):
        raise SystemExit(f"release metadata check failed: {problem}")
    print("release metadata: PASS")


if __name__ == "__main__":
    main()
