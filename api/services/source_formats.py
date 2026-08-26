# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Source format parsers.

Every parser produces the same canonical representation:

    (metadata: dict, body: str)

so format-specific semantics never leak into retrieval. Markdown is the
canonical format; other formats are converted to it.
"""

from __future__ import annotations

from pathlib import Path

from api.services.parser import parse_markdown


def parse_file(path: str | Path) -> tuple[dict, str]:
    """Parse a file into (metadata, body) based on its extension.

    Supported: .md/.markdown (canonical), .txt, source code, .json, .yaml/.yml.
    Raises ValueError for unsupported extensions.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    content = p.read_text(encoding="utf-8")

    if suffix in (".md", ".markdown"):
        return parse_markdown(content)
    if suffix == ".txt":
        return _parse_plain_text(p, content)
    if suffix in _CODE_EXTENSIONS:
        return _parse_code(p, content)
    if suffix == ".json":
        return _parse_json(p, content)
    if suffix in (".yaml", ".yml"):
        return _parse_yaml(p, content)

    raise ValueError(f"unsupported source format: {suffix}")


def _synthesized_metadata(
    title: str,
    doc_type: str,
    tags: list[str],
    summary: str,
) -> dict:
    """Build frontmatter-equivalent metadata for non-Markdown sources."""
    return {
        "title": title,
        "date": None,
        "tags": tags,
        "document_type": doc_type if doc_type in ("kaggle", "project", "note", "paper") else "note",
        "summary": summary,
    }


def _parse_plain_text(p: Path, content: str) -> tuple[dict, str]:
    first_line = next((ln.strip() for ln in content.splitlines() if ln.strip()), p.stem)
    title = first_line[:120]
    meta = _synthesized_metadata(title, "note", ["text"], f"Plain text document: {p.name}")
    # Promote the first line to a Markdown heading so chunking keeps context.
    body = f"# {title}\n\n{content.strip()}"
    return meta, body


def _parse_code(p: Path, content: str) -> tuple[dict, str]:
    lang = _CODE_EXTENSIONS[p.suffix.lower()]
    title = f"{p.name} ({lang})"
    meta = _synthesized_metadata(title, "note", ["code", lang], f"Source code file {p.name}")
    fenced = f"# {p.name}\n\n```{lang}\n{content.strip()}\n```\n"
    return meta, fenced


def _parse_json(p: Path, content: str) -> tuple[dict, str]:
    import json as _json

    try:
        data = _json.loads(content)
        keys = ", ".join(list(data.keys())[:10]) if isinstance(data, dict) else ""
        summary = f"JSON document with keys: {keys}" if keys else "JSON document"
    except _json.JSONDecodeError:
        summary = "JSON document (unparseable)"
    title = p.stem
    meta = _synthesized_metadata(title, "note", ["json"], summary)
    fenced = f"# {p.stem}\n\n```json\n{content.strip()}\n```\n"
    return meta, fenced


def _parse_yaml(p: Path, content: str) -> tuple[dict, str]:
    import yaml as _yaml

    try:
        data = _yaml.safe_load(content)
        keys = ", ".join(list(data.keys())[:10]) if isinstance(data, dict) else ""
        summary = f"YAML document with keys: {keys}" if keys else "YAML document"
    except _yaml.YAMLError:
        summary = "YAML document (unparseable)"
    title = p.stem
    meta = _synthesized_metadata(title, "note", ["yaml"], summary)
    fenced = f"# {p.stem}\n\n```yaml\n{content.strip()}\n```\n"
    return meta, fenced


_CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".sh": "bash",
    ".sql": "sql",
}

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"} | set(
    _CODE_EXTENSIONS.keys()
)
