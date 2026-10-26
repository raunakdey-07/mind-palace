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

    if suffix in (".md", ".markdown"):
        content = p.read_text(encoding="utf-8")
        return parse_markdown(content)
    if suffix == ".txt":
        content = p.read_text(encoding="utf-8")
        return _parse_plain_text(p, content)
    if suffix in _CODE_EXTENSIONS:
        content = p.read_text(encoding="utf-8")
        return _parse_code(p, content)
    if suffix == ".json":
        content = p.read_text(encoding="utf-8")
        return _parse_json(p, content)
    if suffix in (".yaml", ".yml"):
        content = p.read_text(encoding="utf-8")
        return _parse_yaml(p, content)
    if suffix == ".docx":
        return _parse_docx(p)
    if suffix == ".pptx":
        return _parse_pptx(p)
    if suffix == ".pdf":
        return _parse_pdf(p)

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

SUPPORTED_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".docx",
    ".pptx",
    ".pdf",
} | set(_CODE_EXTENSIONS.keys())


def _parse_docx(p):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    W = "{%s}" % ns
    try:
        z = _zipfile.ZipFile(str(p))
    except _zipfile.BadZipFile:
        raise ValueError("invalid docx")
    try:
        doc_xml = z.read("word/document.xml")
    except KeyError:
        raise ValueError("unsupported source format: .docx")
    root = _ET.fromstring(doc_xml)
    body = root.find(W + "body")
    lines = []
    if body is not None:
        for child in body:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                ppr = child.find(W + "pPr")
                pstyle = None
                if ppr is not None:
                    ps = ppr.find(W + "pStyle")
                    if ps is not None:
                        pstyle = ps.get(W + "val")
                texts = [t.text or "" for t in child.iter(W + "t")]
                text = "".join(texts)
                if not text.strip():
                    lines.append("")
                    continue
                if pstyle and pstyle.lower().startswith("heading"):
                    m = _re2.search(r"(\d+)", pstyle)
                    level = min(int(m.group(1)), 6) if m else 1
                    lines.append("#" * level + " " + text.strip())
                else:
                    lines.append(text.strip())
            elif tag == "tbl":
                rows = []
                for tr in child.findall(W + "tr"):
                    cells = []
                    for tc in tr.findall(W + "tc"):
                        ctext = "".join(t.text or "" for t in tc.iter(W + "t"))
                        cells.append(ctext.strip().replace("|", "\\|"))
                    rows.append(cells)
                if not rows:
                    continue
                header = rows[0]
                lines.append("| " + " | ".join(header) + " |")
                lines.append("| " + " | ".join(["---"] * len(header)) + " |")
                for r in rows[1:]:
                    while len(r) < len(header):
                        r.append("")
                    lines.append("| " + " | ".join(r[: len(header)]) + " |")
                lines.append("")
    body_text = "\n".join(lines).strip()
    title = p.stem
    try:
        cp = z.read("docProps/core.xml")
        croot = _ET.fromstring(cp)
        for el in croot.iter():
            if el.tag.endswith("}title") and el.text:
                title = el.text.strip()
    except KeyError:
        pass
    meta = _synthesized_metadata(title, "note", ["docx"], "DOCX document: %s" % p.name)
    body = "# %s\n\n%s" % (title, body_text) if body_text else "# %s\n" % title
    return meta, body


def _parse_pptx(p):
    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    AT = "{%s}" % A
    try:
        z = _zipfile.ZipFile(str(p))
    except _zipfile.BadZipFile:
        raise ValueError("invalid pptx")
    slide_files = sorted(
        [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")],
        key=lambda x: (
            int(_re2.search(r"slide(\d+)", x).group(1)) if _re2.search(r"slide(\d+)", x) else 0
        ),
    )
    lines = []
    title = p.stem
    for idx, sf in enumerate(slide_files, start=1):
        try:
            root = _ET.fromstring(z.read(sf))
        except Exception:
            continue
        texts = [t.text for t in root.iter(AT + "t") if t.text]
        notes_text = ""
        rels_path = sf.replace("slides/slide", "slides/_rels/slide").replace(".xml", ".xml.rels")
        notes_file = None
        try:
            rels = _ET.fromstring(z.read(rels_path))
            for rel in rels:
                if "notesSlide" in (rel.get("Type") or ""):
                    target = rel.get("Target", "")
                    notes_file = "ppt/" + target.lstrip("../").lstrip("/")
        except KeyError:
            pass
        if notes_file:
            try:
                nroot = _ET.fromstring(z.read(notes_file))
                ntexts = [t.text for t in nroot.iter(AT + "t") if t.text]
                notes_text = " ".join(ntexts).strip()
            except KeyError:
                pass
        slide_title = texts[0].strip() if texts else None
        heading = slide_title if slide_title else "Slide %d" % idx
        lines.append("## Slide %d: %s" % (idx, heading))
        lines.append("")
        for t in texts[1:]:
            t = t.strip()
            if t:
                lines.append("- " + t)
        if notes_text:
            lines.append("")
            lines.append("> Speaker notes: " + notes_text)
        lines.append("")
    body_text = "\n".join(lines).strip()
    try:
        cp = z.read("docProps/core.xml")
        croot = _ET.fromstring(cp)
        for el in croot.iter():
            if el.tag.endswith("}title") and el.text:
                title = el.text.strip()
    except KeyError:
        pass
    meta = _synthesized_metadata(title, "note", ["pptx"], "PPTX presentation: %s" % p.name)
    body = "# %s\n\n%s" % (title, body_text) if body_text else "# %s\n" % title
    return meta, body


def _parse_pdf(p):
    text = ""
    try:
        if _shutil.which("pdftotext"):
            out = _subprocess.run(
                ["pdftotext", "-layout", str(p), "-"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if out.returncode == 0:
                text = out.stdout
    except Exception:
        text = ""
    if not text.strip():
        text = _pdf_stdlib_extract(p)
    title = p.stem
    meta = _synthesized_metadata(title, "note", ["pdf"], "PDF document: %s" % p.name)
    pages = text.split("\f")
    lines = []
    for i, pg in enumerate(pages, start=1):
        pg = pg.strip()
        if not pg:
            continue
        lines.append("## Page %d" % i)
        lines.append("")
        lines.append(pg)
        lines.append("")
    body_text = "\n".join(lines).strip()
    body = "# %s\n\n%s" % (title, body_text) if body_text else "# %s\n" % title
    return meta, body


def _pdf_stdlib_extract(p):
    try:
        data = p.read_bytes()
    except Exception:
        return ""
    out = []
    for m in _re2.finditer(rb"stream\r?\n(.*?)endstream", data, _re2.DOTALL):
        raw = m.group(1)
        try:
            d = _zlib.decompress(raw.strip())
        except Exception:
            d = raw
        for tm in _re2.finditer(rb"\((?:\\.|[^()])*\)\s*Tj", d):
            s = tm.group(0)
            start = s.find(b"(")
            end = s.rfind(b")")
            if start != -1 and end != -1:
                chunk = s[start + 1 : end]
                chunk = chunk.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                try:
                    out.append(chunk.decode("latin-1"))
                except Exception:
                    pass
        for tm in _re2.finditer(rb"\[(.*?)\]\s*TJ", d, _re2.DOTALL):
            arr = tm.group(1)
            for sm in _re2.finditer(rb"\((?:\\.|[^()])*\)", arr):
                s = sm.group(0)
                chunk = s[1:-1]
                chunk = chunk.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                try:
                    out.append(chunk.decode("latin-1"))
                except Exception:
                    pass
    return " ".join(out)


import zipfile as _zipfile  # noqa: E402
import xml.etree.ElementTree as _ET  # noqa: E402
import zlib as _zlib  # noqa: E402
import re as _re2  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import shutil as _shutil  # noqa: E402
