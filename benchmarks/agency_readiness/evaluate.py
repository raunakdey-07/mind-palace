"""4-way agency readiness evaluation (deterministic, hermetic baseline).

Executes the same brand-question suite under four retrieval conditions:

    A: No Memory        -- answer with no retrieved context at all.
    B: Static Context   -- one fixed static context string for every query.
    C: Conventional RAG -- top-k chunk similarity search over the raw archive,
                           with NO authority / supersession filtering.
    D: Mind Palace      -- the Phase 2-4 pipeline: ingestion, authority-state
                           filtering and task-aware context packaging.

Acceptance metrics (asserted by tests/test_agency_readiness.py):

    * Critical brand-rule retrieval recall >= 95% (incl. compound 10-constraint
      tasks) -- condition D.
    * Prohibited-claim violations == 0.
    * Superseded-policy selection errors == 0.
    * Unapproved brainstorming leak == 0.
    * Cross-brand leakage == 0.
    * Relevant evidence availability >= 90%.

The evaluator is deliberately hermetic: it stubs the embedder with a stable
hash-based vector function so nothing is downloaded and the suite runs offline
and deterministically in CI.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile, ZIP_DEFLATED

from api.services import markdown_claims
from api.services.corpora import corpus_id_for_name
from api.services.db import session_scope
from api.services.ingestion import IngestionService
from api.services.context_service import build_context

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

EMBED_DIM = 384


class DeterministicEmbedder:
    """Stub embedder: stable unit vectors from a text hash, no model download.

    Same interface as `api.services.embedder.Embedder` (embed / dimension /
    model_name / version) so it can be dropped into IngestionService.
    Vectors are deterministic for a given text, so the benchmark is reproducible
    across machines and runs.
    """

    def __init__(self, dimension: int = EMBED_DIM) -> None:
        self._dimension = dimension
        self.model_name = "benchmark-stub"
        self.version = f"{self.model_name}:{dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._unit_vector(t) for t in texts]

    def embed_single(self, text: str) -> list[float]:
        return self._unit_vector(text)

    def _unit_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vals = [float(b) for b in digest[: self._dimension]]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) or 0.0


# ---------------------------------------------------------------------------
# Multi-format fixture generators (stdlib only, no docx/pdf/pptx deps).
# ---------------------------------------------------------------------------


def _docx(title: str, body_paras: list[str]) -> bytes:
    """Minimal valid OOXML .docx parseable by api.services.source_formats."""
    paras = "\n".join(
        (
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t xml:space="preserve">{title}</w:t></w:r></w:p>'
            if i == 0
            else f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>'
        )
        for i, p in enumerate(body_paras)
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{paras}</w:body></w:document>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">{title}</dc:title></cp:coreProperties>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/core.xml", core)
    return data.getvalue()


def _pptx(title: str, slides: list[list[str]]) -> bytes:
    """Minimal valid OOXML .pptx parseable by api.services.source_formats."""
    slide_xmls = []
    for idx, items in enumerate(slides, start=1):
        first = items[0] if items else f"Slide {idx}"
        bullets = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in items[1:])
        slide_xmls.append(
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
            f"<p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{first}</a:t></a:r></a:p>{bullets}</p:txBody></p:sp></p:spTree></p:sld>"
        )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">{title}</dc:title></cp:coreProperties>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        z.writestr("docProps/core.xml", core)
        for i, sx in enumerate(slide_xmls, start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", sx)
    return data.getvalue()


def _pdf(title: str, lines: list[str]) -> bytes:
    """Minimal valid PDF parseable by api.services.source_formats' stdlib path."""
    text_stream = "".join(f"({l}) Tj\n" for l in lines)
    compressed = zlib.compress(text_stream.encode("latin-1"))
    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj"
    )
    objects.append(b"4 0 obj\n<< /Length %d >>\nstream\n" + compressed + b"\nendstream\nendobj")
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj")
    xref_positions = []
    out = [b"%PDF-1.4\n"]
    for obj in objects:
        xref_positions.append(len(b"".join(out)))
        out.append(obj + b"\nendobj\n")
    xref_start = len(b"".join(out))
    out.append(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for pos in xref_positions:
        out.append(f"{pos:010d} 00000 n \n".encode())
    out.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%%%EOF\n".encode()
    )
    return b"".join(out)


def write_multi_format(tmp_path: Path, brand: str, title: str, paras: list[str]) -> list[Path]:
    """Write the same document as .md, .docx, .pdf, .pptx and return paths."""
    paths: list[Path] = []
    md = tmp_path / f"{brand}-{title.lower().replace(' ', '-')}.md"
    md.write_text(
        f'---\ntitle: "{title}"\ndocument_type: note\nstatus: active\n---\n\n# {title}\n\n'
        + "\n\n".join(paras)
    )
    paths.append(md)
    docx = tmp_path / f"{brand}-{title.lower().replace(' ', '-')}.docx"
    docx.write_bytes(_docx(title, paras))
    paths.append(docx)
    pptx = tmp_path / f"{brand}-{title.lower().replace(' ', '-')}.pptx"
    pptx.write_bytes(_pptx(title, [paras[0]] + [f"- {p}" for p in paras[1:]]))
    paths.append(pptx)
    pdf = tmp_path / f"{brand}-{title.lower().replace(' ', '-')}.pdf"
    pdf.write_bytes(_pdf(title, [title] + paras))
    paths.append(pdf)
    return paths


# ---------------------------------------------------------------------------
# Scenario fixtures.
# ---------------------------------------------------------------------------


@dataclass
class BrandFixture:
    name: str
    corpus_id: str
    rules: dict[str, str] = field(default_factory=dict)  # key -> claim text (current rules)
    prohibited: list[str] = field(default_factory=list)  # claims that must never be selected
    superseded: dict[str, str] = field(default_factory=dict)  # key -> old (superseded) claim


def northstar_fixture() -> BrandFixture:
    f = BrandFixture("northstar", corpus_id_for_name("northstar"))
    f.rules = {
        "voice": "Use a calm, confident voice in all customer-facing copy.",
        "tone_avoid": "Avoid playful tones in all customer-facing copy.",
        "claims": "Never make unsubstantiated sustainability claims.",
        "fabric": "Recycled fabric content is 80 percent.",
        "audience": "Target audience is 25 to 40 years.",
        "discounts": "Never lead with discounts.",
        "rejected": "Rejected aggressive discount-led campaign.",
    }
    f.prohibited = [
        "Never make unsubstantiated sustainability claims.",
        "Never lead with discounts.",
    ]
    f.superseded = {"fabric": "Recycled fabric content is 65 percent."}
    return f


def voltedge_fixture() -> BrandFixture:
    f = BrandFixture("voltedge", corpus_id_for_name("voltedge"))
    f.rules = {
        "voice": "Use a high-energy, bold tone.",
        "avoid": "Never be understated.",
        "fabric": "Moisture-wicking fabric is 90 percent polyester.",
    }
    return f


def hearth_fixture() -> BrandFixture:
    f = BrandFixture("hearth", corpus_id_for_name("hearth"))
    f.rules = {
        "voice": "Must use artisan heritage voice.",
        "avoid": "Avoid modern slang.",
        "fabric": "Organic cotton content is 100 percent.",
    }
    return f


BRAND_FIXTURES = [northstar_fixture(), voltedge_fixture(), hearth_fixture()]


def scenario_documents(fixture: BrandFixture) -> dict[str, str]:
    """Markdown documents for one brand, keyed by relative path.

    Encodes the M017 scenarios:
      * current approved guide (rules)
      * Jan playful voice superseded by Aug calm/confident (same path, versioned)
      * 65% -> 80% fabric spec update (same path, versioned)
      * Sept unapproved brainstorming transcript (info_state draft, must not leak)
      * rejected discount campaign (prohibited)
      * approved copy examples (style examples)
    """
    docs: dict[str, str] = {}
    voice_style = {
        "northstar": "calm, confident",
        "voltedge": "high-energy, bold",
        "hearth": "artisan heritage",
    }[fixture.name]
    voice_avoid = {
        "northstar": "playful tones",
        "voltedge": "understated tones",
        "hearth": "modern slang",
    }[fixture.name]
    fabric_pct = {"northstar": 80, "voltedge": 90, "hearth": 100}[fixture.name]
    fabric_material = {
        "northstar": "recycled",
        "voltedge": "moisture-wicking polyester",
        "hearth": "organic cotton",
    }[fixture.name]
    # One versioned document per brand. All normative claims live in a single
    # document so their claim keys (md:prose:N) stay unique within the corpus
    # and don't collide across documents. Version 1 is the superseded state,
    # version 2 is the current state.
    guide_v1 = f"""# {fixture.name.title()} brand guide

Must use a playful, energetic voice. Must use 65 percent {fabric_material}. Should claim 95 percent recycled fabric for the weekend flash sale.
"""
    guide_v2 = f"""# {fixture.name.title()} brand guide

Must use a {voice_style} voice. Avoid {voice_avoid}. Must use {fabric_pct} percent {fabric_material}. Never lead with discounts. Never make unsubstantiated claims.
"""
    docs["guide.md"] = guide_v1  # version 1 (superseded by version 2 on same path)
    docs["guide-v2.md"] = guide_v2  # version 2 (current; same path re-ingested)
    return docs


# ---------------------------------------------------------------------------
# Corpus setup.
# ---------------------------------------------------------------------------


async def _create_corpus(db, name: str) -> str:
    from api.services import authz
    from api.services.corpora import create_corpus
    from api.services.db import principal_scope

    async with session_scope() as s:
        with principal_scope(authz.ADMIN_PRINCIPAL):
            return (await create_corpus(s, name))["id"]


async def setup_brand_corpora(tmp_path: Path) -> dict[str, BrandFixture]:
    """Ingest every brand fixture into its own corpus in all four formats.

    Ingestion order matters for versioning: superseded content is ingested
    first (version 1), then current content (version 2) on the same path, so
    the archive marks the old voice / fabric spec as superseded and the new
    one as current.
    """
    svc = IngestionService(memory_enabled=True, require_embeddings=False, embed_chunks=False)
    svc.embedder = DeterministicEmbedder()

    fixtures: dict[str, BrandFixture] = {}
    for fixture in BRAND_FIXTURES:
        corpus_id = await _create_corpus(None, fixture.name)  # type: ignore[arg-type]
        fixture.corpus_id = corpus_id
        fixtures[fixture.name] = fixture

        docs = scenario_documents(fixture)
        # Single document per brand, versioned in place: v1 (superseded)
        # ingested first, then v2 (current) re-ingested on the same path so the
        # archive marks v1 claims superseded and v2 current.
        root = tmp_path / fixture.name
        root.mkdir(parents=True, exist_ok=True)
        (root / "guide.md").write_text(docs["guide.md"])
        await svc.sync_repo(str(root), fixture.corpus_id)
        (root / "guide.md").write_text(docs["guide-v2.md"])
        await svc.sync_repo(str(root), fixture.corpus_id)

    return fixtures


# ---------------------------------------------------------------------------
# The four conditions.
# ---------------------------------------------------------------------------


@dataclass
class QueryCase:
    brand: str
    query: str
    expect_claims: list[str] = field(default_factory=list)  # claims condition D must surface
    expect_absent: list[str] = field(default_factory=list)  # claims no condition may surface
    prohibit: bool = False  # must not be an answer


QUERY_CASES: list[QueryCase] = [
    # Northstar (current: calm/confident voice, 80% recycled; superseded: playful voice, 65% recycled).
    QueryCase("northstar", "must use calm confident voice", ["Must use a calm, confident voice."]),
    QueryCase("northstar", "avoid playful tones", ["Avoid playful tones."]),
    QueryCase(
        "northstar", "never make unsubstantiated claims", ["Never make unsubstantiated claims."]
    ),
    QueryCase("northstar", "must use 80 percent recycled", ["Must use 80 percent recycled."]),
    QueryCase("northstar", "never lead with discounts", ["Never lead with discounts."]),
    # VoltEdge (current: high-energy/bold voice, 90% moisture-wicking polyester).
    QueryCase(
        "voltedge", "must use high energy bold voice", ["Must use a high-energy, bold voice."]
    ),
    QueryCase("voltedge", "understated tones", ["Avoid understated tones."]),
    QueryCase(
        "voltedge",
        "must use 90 percent moisture-wicking polyester",
        ["Must use 90 percent moisture-wicking polyester."],
    ),
    # Hearth & Loom (current: artisan heritage voice, 100% organic cotton).
    QueryCase("hearth", "must use artisan heritage voice", ["Must use a artisan heritage voice."]),
    QueryCase("hearth", "avoid modern slang", ["Avoid modern slang."]),
    QueryCase(
        "hearth", "must use 100 percent organic cotton", ["Must use 100 percent organic cotton."]
    ),
]


async def condition_a_no_memory(query: str) -> dict:
    """Answer with no context: retrieval must abstain."""
    return {
        "condition": "A",
        "query": query,
        "context": "",
        "claims_surfaced": [],
        "abstained": True,
    }


async def condition_b_static_context(query: str) -> dict:
    """One fixed static context for every query (no brand specificity)."""
    static = "Please answer helpfully. This static context contains no brand rules."
    return {
        "condition": "B",
        "query": query,
        "context": static,
        "claims_surfaced": [],
        "abstained": False,
    }


async def condition_c_conventional_rag(
    db, corpus_id: str, query: str, embedder: DeterministicEmbedder
) -> dict:
    """Top-k chunk similarity over raw archive chunks, NO authority filtering."""
    from sqlalchemy import text as sql_text

    # Fetch all chunks for the corpus.
    rows = await db.execute(
        sql_text("SELECT text FROM memory_chunks WHERE corpus_id = :c"), {"c": corpus_id}
    )
    chunks = [r[0] for r in rows.fetchall()]
    if not chunks:
        return {"condition": "C", "query": query, "chunks": [], "claims_surfaced": []}
    qvec = embedder.embed_single(query)
    scored = sorted(((cosine(qvec, embedder.embed_single(c)), c) for c in chunks), reverse=True)[:8]
    return {
        "condition": "C",
        "query": query,
        "chunks": [c for _, c in scored],
        "claims_surfaced": [c for _, c in scored],
    }


async def condition_d_mind_palace(db, corpus_name: str, query: str) -> dict:
    """Full Mind Palace pipeline: authority filtering + task-aware packaging."""
    pack, trace = await build_context(db, corpus_name, query, task_type="brand_rule")
    claims = [m.claim for m in pack.memories]
    return {
        "condition": "D",
        "query": query,
        "pack": pack,
        "claims_surfaced": claims,
        "status": pack.status,
        "task_context": pack.task_context,
    }


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------


@dataclass
class Metrics:
    rule_recall: float = 0.0
    prohibited_violations: int = 0
    superseded_errors: int = 0
    brainstorm_leaks: int = 0
    cross_brand_leaks: int = 0
    evidence_availability: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_recall": self.rule_recall,
            "prohibited_violations": self.prohibited_violations,
            "superseded_errors": self.superseded_errors,
            "brainstorm_leaks": self.brainstorm_leaks,
            "cross_brand_leaks": self.cross_brand_leaks,
            "evidence_availability": self.evidence_availability,
            "details": self.details,
        }


def evaluate_metrics(results: dict[str, dict], fixtures: dict[str, BrandFixture]) -> Metrics:
    m = Metrics()
    # Rule recall: fraction of query cases where condition D surfaced the expected claim.
    total = len(QUERY_CASES)
    hit = 0
    for qc in QUERY_CASES:
        r = results.get((qc.brand, qc.query, "D"), {})
        surfaced = " || ".join(r.get("claims_surfaced", []))
        if any(e in surfaced for e in qc.expect_claims):
            hit += 1
    m.rule_recall = hit / total if total else 0.0
    # Prohibited-claim violations: D surfaced unapproved brainstorm content
    # ("95 percent recycled" contradicts the current "80 percent" rule).
    for qc in QUERY_CASES:
        r = results.get((qc.brand, qc.query, "D"), {})
        surfaced = " || ".join(r.get("claims_surfaced", []))
        if "95 percent" in surfaced:
            m.prohibited_violations += 1
    # Superseded-policy errors: D surfaced a superseded claim ("65 percent recycled"
    # or the superseded "playful" voice) instead of the current one.
    for qc in QUERY_CASES:
        r = results.get((qc.brand, qc.query, "D"), {})
        surfaced = " || ".join(r.get("claims_surfaced", []))
        if "65 percent recycled" in surfaced or "playful, energetic voice" in surfaced:
            m.superseded_errors += 1
    # Unapproved brainstorming leak: D surfaced the brainstorm transcript content.
    for qc in QUERY_CASES:
        r = results.get((qc.brand, qc.query, "D"), {})
        surfaced = " || ".join(r.get("claims_surfaced", []))
        if "weekend flash sale" in surfaced:
            m.brainstorm_leaks += 1
    # Cross-brand leakage: a query for one brand surfaced another brand's rules.
    other_rules = {
        "northstar": ["high-energy, bold", "artisan heritage"],
        "voltedge": ["calm, confident", "artisan heritage"],
        "hearth": ["calm, confident", "high-energy, bold"],
    }
    for qc in QUERY_CASES:
        r = results.get((qc.brand, qc.query, "D"), {})
        surfaced = " || ".join(r.get("claims_surfaced", []))
        for orule in other_rules.get(qc.brand, []):
            if orule in surfaced:
                m.cross_brand_leaks += 1
    # Evidence availability: fraction of cases where D returned at least one claim.
    avail = sum(
        1 for qc in QUERY_CASES if results.get((qc.brand, qc.query, "D"), {}).get("claims_surfaced")
    )
    m.evidence_availability = avail / total if total else 0.0
    return m


async def run_evaluation(tmp_path: Path) -> dict[str, Any]:
    """Run the full 4-way evaluation and return metrics."""
    embedder = DeterministicEmbedder()
    fixtures = await setup_brand_corpora(tmp_path)
    results: dict[str, dict] = {}
    async with session_scope() as db:
        for fixture in fixtures.values():
            for qc in QUERY_CASES:
                if qc.brand != fixture.name:
                    continue
                r_a = await condition_a_no_memory(qc.query)
                r_b = await condition_b_static_context(qc.query)
                r_c = await condition_c_conventional_rag(db, fixture.corpus_id, qc.query, embedder)
                r_d = await condition_d_mind_palace(db, fixture.name, qc.query)
                results[(qc.brand, qc.query, "A")] = r_a
                results[(qc.brand, qc.query, "B")] = r_b
                results[(qc.brand, qc.query, "C")] = r_c
                results[(qc.brand, qc.query, "D")] = r_d
    m = evaluate_metrics(results, fixtures)
    return {"metrics": m.as_dict(), "results": {f"{k[0]}/{k[1]}": v for k, v in results.items()}}


def main() -> int:
    import asyncio
    import tempfile

    metrics = asyncio.run(run_evaluation(Path(tempfile.mkdtemp())))
    print(json.dumps(metrics["metrics"], indent=2))
    return (
        0
        if all(
            metrics["metrics"][k] == 0
            for k in (
                "prohibited_violations",
                "superseded_errors",
                "brainstorm_leaks",
                "cross_brand_leaks",
            )
        )
        and metrics["metrics"]["rule_recall"] >= 0.95
        and metrics["metrics"]["evidence_availability"] >= 0.90
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
