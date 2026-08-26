"""Tests for source format parsers (Milestone 14)."""

from __future__ import annotations

import pytest

from api.services.source_formats import parse_file


def test_markdown_passthrough(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text('---\ntitle: "T"\ndate: 2024-01-01\n---\n\n# Body\n\ntext')
    meta, body = parse_file(f)
    assert meta["title"] == "T"
    assert "# Body" in body


def test_plain_text_gets_title_heading(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("First line is the title.\n\nSome content follows.")
    meta, body = parse_file(f)
    assert meta["title"] == "First line is the title."
    assert body.startswith("# First line is the title.")
    assert "Some content follows." in body
    assert meta["document_type"] == "note"


def test_code_file_fenced_with_language(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("def main():\n    return 42\n")
    meta, body = parse_file(f)
    assert meta["title"] == "app.py (python)"
    assert "```python" in body
    assert "def main():" in body
    assert any("code" in t for t in meta["tags"])


def test_json_extracts_keys_in_summary(tmp_path):
    f = tmp_path / "config.json"
    f.write_text('{"alpha": 1, "beta": 2}')
    meta, body = parse_file(f)
    assert "alpha" in meta["summary"]
    assert "```json" in body


def test_yaml_extracts_keys_in_summary(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text("key1: value1\nkey2: value2\n")
    meta, body = parse_file(f)
    assert "key1" in meta["summary"]
    assert "```yaml" in body


def test_malformed_json_still_parses_with_warning_summary(tmp_path):
    f = tmp_path / "broken.json"
    f.write_text("{not valid json")
    meta, body = parse_file(f)
    assert "unparseable" in meta["summary"]
    assert body  # content preserved regardless


def test_unsupported_extension_raises(tmp_path):
    f = tmp_path / "image.bin"
    f.write_text("\x00\x01\x02")
    with pytest.raises(ValueError, match="unsupported"):
        parse_file(f)


@pytest.mark.parametrize(
    "name,expected_lang",
    [("s.ts", "typescript"), ("m.go", "go"), ("q.rs", "rust"), ("q.sql", "sql")],
)
def test_code_language_detection(tmp_path, name, expected_lang):
    f = tmp_path / name
    f.write_text("// code")
    meta, body = parse_file(f)
    assert f"```{expected_lang}" in body


def test_deterministic_output(tmp_path):
    f = tmp_path / "det.txt"
    f.write_text("Title line\nbody")
    r1 = parse_file(f)
    r2 = parse_file(f)
    assert r1 == r2
