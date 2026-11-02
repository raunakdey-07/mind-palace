"""check_versioning.py must compare the doc-named migration head with the real alembic head.

Red first: STATUS.md names `012_claim_embedding_hnsw` while the real head is
`015_rls_scope_enforcement`, so the check must report a mismatch. After
STATUS.md is fixed to `015_rls_scope_enforcement`, the check passes.

Edge cases (fail loud): zero heads, multiple heads (named), and any migration
file whose revision/down_revision cannot be parsed. Annotated assignments
(`down_revision: str | None = "x"`) and tuple/list merge revisions are
supported.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_versioning():
    return _load("check_versioning", "scripts/release/check_versioning.py")


def _status_text() -> str:
    return (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")


def _real_versions_dir() -> Path:
    return ROOT / "migrations" / "versions"


def _write_migration(tmp_path: Path, name: str, revision: str, down_revision) -> None:
    """Write a migration file whose down_revision renders via !r (None, str,
    tuple, list all supported by the parser)."""
    content = (
        f'"""migration {name}"""\n'
        f'revision = "{revision}"\n'
        f"down_revision = {down_revision!r}\n"
    )
    (tmp_path / f"{name}.py").write_text(content, encoding="utf-8")


def test_migration_head_problems_detects_stale_doc():
    """A doc naming a different revision than the real head must be flagged.

    The fake doc's revision is built from ``real_alembic_head()`` plus a
    suffix so nothing is hardcoded.
    """
    module = _check_versioning()
    head = module.real_alembic_head(_real_versions_dir())
    assert head is not None
    fake_rev = head + "_nonexistent"
    fake = f"| migration head | `{fake_rev}`, additive and reversible |"
    problems = module.migration_head_problems(fake, _real_versions_dir())
    assert len(problems) == 1, problems
    assert fake_rev in problems[0] and head in problems[0], problems[0]


def test_migration_head_matches_real_status_doc():
    """STATUS.md must name the real alembic head (no mismatch)."""
    module = _check_versioning()
    problems = module.migration_head_problems(_status_text(), _real_versions_dir())
    assert problems == [], problems


def test_real_alembic_head_returns_real_head():
    """Independent oracle: alembic's own ScriptDirectory must agree with our
    file-scanning implementation."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _check_versioning()
    oracle = ScriptDirectory.from_config(
        Config(str(ROOT / "migrations" / "alembic.ini"))
    ).get_current_head()
    assert module.real_alembic_head(_real_versions_dir()) == oracle


def test_zero_heads_is_a_problem(tmp_path):
    """Every migration referencing another => no head => fail loud."""
    module = _check_versioning()
    _write_migration(tmp_path, "001_a", "001_a", "002_b")
    _write_migration(tmp_path, "002_b", "002_b", "001_a")
    problems = module.migration_head_problems("", tmp_path)
    assert problems, problems
    assert "no alembic head found" in problems[0], problems[0]


def test_multiple_heads_are_named(tmp_path):
    """Two unreferenced migrations => multiple heads, both named."""
    module = _check_versioning()
    _write_migration(tmp_path, "001_a", "001_a", None)
    _write_migration(tmp_path, "002_b", "002_b", None)
    problems = module.migration_head_problems("", tmp_path)
    assert problems, problems
    assert "multiple alembic heads" in problems[0], problems[0]
    assert "001_a" in problems[0] and "002_b" in problems[0], problems[0]


def test_unparseable_revision_is_a_problem(tmp_path):
    """A migration with no parseable revision => fail loud."""
    module = _check_versioning()
    (tmp_path / "001_broken.py").write_text(
        '"""broken"""\nrevision = some_variable\n', encoding="utf-8"
    )
    problems = module.migration_head_problems("", tmp_path)
    assert problems, problems
    assert "could not parse revision" in problems[0], problems[0]


def test_unparseable_down_revision_is_a_problem(tmp_path):
    """A migration whose down_revision is a variable => fail loud."""
    module = _check_versioning()
    _write_migration(tmp_path, "001_a", "001_a", None)
    (tmp_path / "002_broken.py").write_text(
        '"""broken"""\nrevision = "002_broken"\ndown_revision = some_variable\n',
        encoding="utf-8",
    )
    problems = module.migration_head_problems("", tmp_path)
    assert problems, problems
    assert "could not parse down_revision" in problems[0], problems[0]


def test_annotated_down_revision_is_supported(tmp_path):
    """down_revision: str | None = \"x\" parses like a plain assignment."""
    module = _check_versioning()
    (tmp_path / "001_a.py").write_text(
        '"""a"""\nrevision = "001_a"\ndown_revision: str | None = None\n',
        encoding="utf-8",
    )
    (tmp_path / "002_b.py").write_text(
        '"""b"""\nrevision: str = "002_b"\ndown_revision: str | None = "001_a"\n',
        encoding="utf-8",
    )
    head = module.real_alembic_head(tmp_path)
    assert head == "002_b", head


def test_merge_down_revision_tuple_is_supported(tmp_path):
    """down_revision = (\"a\", \"b\") references both parents."""
    module = _check_versioning()
    _write_migration(tmp_path, "001_a", "001_a", None)
    _write_migration(tmp_path, "002_b", "002_b", None)
    _write_migration(tmp_path, "003_c", "003_c", ("001_a", "002_b"))
    head = module.real_alembic_head(tmp_path)
    assert head == "003_c", head


def test_merge_down_revision_list_is_supported(tmp_path):
    """down_revision = [\"a\", \"b\"] references both parents."""
    module = _check_versioning()
    _write_migration(tmp_path, "001_a", "001_a", None)
    _write_migration(tmp_path, "002_b", "002_b", None)
    _write_migration(tmp_path, "003_c", "003_c", ["001_a", "002_b"])
    head = module.real_alembic_head(tmp_path)
    assert head == "003_c", head
