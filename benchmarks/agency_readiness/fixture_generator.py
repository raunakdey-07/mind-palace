"""Deterministic fixture generator for the M018 sealed benchmark.

Spreads each brand's gold across multiple documents, uses neutral-volume
filler, and produces separate dev and sealed fixture sets. No Mind Palace
imports; no tuning to Mind Palace vocabulary.

Layout per brand (every content doc 300-800 words, carries at most one
standing gold item so no single document holds all of a category):

  * 5 prohibition docs   (standing_prohibitions, 1 gold item each)
  * 2 voice docs         (required_voice_descriptors, 1 gold item each)
  * 2 spec docs          (approved_specs, 1 gold item each)
  * headings.md          (T1: state via headings, approved; trap coverage)
  * t2_policy_v1.md      (superseded: old policy, T2 old version)
  * t2_policy_v2.md      (approved: new policy with rule at position 1, T2)
  * september_brainstorm.md (draft: draft markers, "We draft emails every Monday.")
  * mixed.md             (T3: bullets + prose + table, hard-wrapped 72)
  * descriptive.md       (trap coverage: mustard-yellow descriptive copy)
  * doc-001.md ... doc-010.md  (neutral filler, no gold)

All versions present on disk are indexed by C; C+ filters by document-level
frontmatter status (current version, not superseded, not draft/rejected/
superseded). Because the `chunks` table holds only the latest version per
doc, v1/v2 are represented as separate paths (t2_policy_v1.md / v2.md).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEALED_SEED = 20261028
DEV_SET_SEED = 777

DEV_FIXTURE_DIR = ROOT / "benchmarks" / "agency_readiness" / "dev_fixtures"
SEALED_FIXTURE_DIR = ROOT / "benchmarks" / "agency_readiness" / "sealed_fixtures"

# Neutral filler sentences (contain none of the gold strings).
FILLER_SENTENCES = [
    "The product ships with a two-year limited warranty covering defects "
    "in materials and workmanship.",
    "Customer support is available by email and phone during business hours, Monday to Friday.",
    "Orders are processed within one business day and shipped with tracking information.",
    "Returns are accepted within thirty days of purchase with original packaging and receipt.",
    "The maintenance schedule recommends inspection every six months for optimal performance.",
    "Software updates are delivered automatically and require a restart to take effect.",
    "Accessibility features include high-contrast mode, keyboard navigation, and screen-reader "
    "support.",
    "The mobile application syncs across devices using end-to-end encryption.",
    "Battery life depends on usage patterns and typically lasts a full day on a single charge.",
    "Installation requires a Phillips-head screwdriver and approximately twenty minutes.",
    "The specifications table lists dimensions, weight, power draw, and operating temperature.",
    "User feedback is collected through surveys and used to prioritize the roadmap.",
    "Pricing varies by region and volume, with discounts for educational and nonprofit customers.",
    "The compliance program addresses data retention, audit trails, and access controls.",
    "Training materials include videos, quick-reference guides, and live webinars.",
    "Warehouse staff perform cycle counts every Friday morning before opening.",
    "The conference room has a whiteboard, a projector, and two video cameras.",
    "Employees may expense reasonable meals while travelling on company business.",
    "The new office location is near the train station and a short walk from the bus stop.",
    "Quarterly results will be presented at the all-hands meeting on the first Thursday.",
    "The API documentation is published internally and updated with every release.",
    "Office hours run from nine to five, with a one-hour lunch at midday.",
    "Remote employees connect through the virtual private network using multi-factor auth.",
    "The design system defines colour, spacing, and typography for all product surfaces.",
    "Customer success holds a weekly review of open support tickets and feature requests.",
]

TARGET_MIN_WORDS = 300
TARGET_MAX_WORDS = 800
FILLER_DOC_COUNT = 12
FILLER_MIN_WORDS = 500
FILLER_MAX_WORDS = 750


def _rng(seed: int, brand: str) -> random.Random:
    return random.Random(f"{seed}:{brand}")


def _tokens(text: str) -> int:
    return max(1, len(text.split()))


def _hard_wrap(text: str, width: int = 72) -> str:
    words = text.split()
    lines = []
    line = []
    cur = 0
    for w in words:
        add = len(w) + (1 if line else 0)
        if cur + add > width and line:
            lines.append(" ".join(line))
            line = [w]
            cur = len(w)
        else:
            line.append(w)
            cur += add
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)


def _frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, str):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {json.dumps(v)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _fill(rng, n: int) -> str:
    sentences = list(FILLER_SENTENCES)
    rng.shuffle(sentences)
    return " ".join(sentences[:n])


def _finalize(body: str, rng: random.Random, min_words: int, max_words: int) -> str:
    """Pad/trim a body so its word count lands in [min_words, max_words]."""
    words = body.split()
    if len(words) > max_words:
        body = " ".join(words[:max_words])
        words = body.split()
    while len(words) < min_words:
        body = body + " " + _fill(rng, max(1, min_words - len(words)))
        words = body.split()
    return body


def generate_brand(brand: str, spec: dict, seed: int) -> dict:
    data = spec["brands"][brand]
    rng = _rng(seed, brand)
    docs: dict[str, str] = {}  # path -> full content with frontmatter
    meta: dict[str, dict] = {}
    template_of: dict[str, str] = {}

    prohibitions = list(data["standing_prohibitions"])
    voice = list(data["required_voice_descriptors"])
    specs_list = list(data["approved_specs"])
    superseded = list(data["superseded_v1_markers"])
    drafts = list(data["unapproved_draft_markers"])

    # ---- 5 prohibition docs (1 standing gold item each) ----
    for i, p in enumerate(prohibitions):
        gold = [f'Do not use the word "{p}" in any customer-facing copy.']
        body = _finalize(" ".join(gold + [_fill(rng, 40)]), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
        path = f"{brand}/prohibition_{i}.md"
        docs[path] = (
            _frontmatter(
                {
                    "title": f"Prohibited vocabulary {i + 1}",
                    "status": "approved",
                    "document_type": "styleguide",
                }
            )
            + body
        )
        meta[path] = {"status": "approved", "document_type": "styleguide"}
        template_of[path] = "prohibition"

    # ---- 2 voice docs (1 standing gold item each) ----
    voice_items = list(voice)
    if len(voice_items) == 1:
        voice_items = voice_items * 2  # single descriptor appears in two docs
    for i, v in enumerate(voice_items):
        gold = [f"Our voice is {v}."]
        body = _finalize(" ".join(gold + [_fill(rng, 40)]), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
        path = f"{brand}/voice_{i}.md"
        docs[path] = (
            _frontmatter(
                {
                    "title": f"Voice descriptor {i + 1}",
                    "status": "approved",
                    "document_type": "styleguide",
                }
            )
            + body
        )
        meta[path] = {"status": "approved", "document_type": "styleguide"}
        template_of[path] = "voice"

    # ---- 2 spec docs (1 standing gold item each) ----
    spec_items = list(specs_list)
    if len(spec_items) == 1:
        spec_items = spec_items * 2
    for i, s in enumerate(spec_items):
        gold = [f"The material is {s}."]
        body = _finalize(" ".join(gold + [_fill(rng, 40)]), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
        path = f"{brand}/spec_{i}.md"
        docs[path] = (
            _frontmatter(
                {
                    "title": f"Approved specification {i + 1}",
                    "status": "approved",
                    "document_type": "spec",
                }
            )
            + body
        )
        meta[path] = {"status": "approved", "document_type": "spec"}
        template_of[path] = "spec"

    # ---- T1: state conveyed by headings (approved, trap coverage) ----
    t1 = []
    t1.append("## Current guidance")
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 12)))
    t1.append("")
    t1.append("## Brainstorming")
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 12)))
    t1.append("")
    t1.append("## Superseded")
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 12)))
    t1.append("")
    t1.append("## Ideas parking lot")
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 8)))
    t1_body = _finalize("\n".join(t1), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    t1_path = f"{brand}/headings.md"
    docs[t1_path] = (
        _frontmatter({"title": "Current guidance", "status": "approved", "document_type": "guide"})
        + t1_body
    )
    meta[t1_path] = {"status": "approved", "document_type": "guide"}
    template_of[t1_path] = "T1"

    # ---- T2 v1 (superseded, old policy) ----
    v1 = []
    v1.append("## Old policy (superseded)")
    v1.append("")
    for m in superseded:
        v1.append(_hard_wrap(f"Previous position: {m}."))
        v1.append("")
    v1.append(_hard_wrap(_fill(rng, 10)))
    v1_body = _finalize("\n".join(v1), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    v1_path = f"{brand}/t2_policy_v1.md"
    docs[v1_path] = (
        _frontmatter({"title": "Old policy", "status": "superseded", "document_type": "policy"})
        + v1_body
    )
    meta[v1_path] = {"status": "superseded", "document_type": "policy"}
    template_of[v1_path] = "T2"

    # ---- T2 v2 (approved, new policy, rule inserted at position 1) ----
    v2 = []
    v2.append("## Current policy")
    v2.append("")
    v2.append('Must never use the word "curated" in public copy.')  # rule inserted at position 1
    v2.append("")
    v2.append(_hard_wrap("Instagram is the primary channel for launches."))
    v2.append("")
    v2.append(_hard_wrap(_fill(rng, 10)))
    v2_body = _finalize("\n".join(v2), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    v2_path = f"{brand}/t2_policy_v2.md"
    docs[v2_path] = (
        _frontmatter({"title": "Current policy", "status": "approved", "document_type": "policy"})
        + v2_body
    )
    meta[v2_path] = {"status": "approved", "document_type": "policy"}
    template_of[v2_path] = "T2"

    # ---- Draft file (trap: unapproved september brainstorm) ----
    draft_content = []
    draft_content.append("## Brainstorming")
    draft_content.append("")
    draft_content.append(_hard_wrap("We draft emails every Monday."))
    draft_content.append("")
    draft_content.append(_hard_wrap("Overnight muscle gain results are expected within weeks."))
    draft_content.append("")
    draft_content.append(_hard_wrap(_fill(rng, 10)))
    draft_body = _finalize("\n".join(draft_content), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    draft_path = f"{brand}/september_brainstorm.md"
    docs[draft_path] = (
        _frontmatter({"title": "September brainstorm", "status": "draft", "document_type": "note"})
        + draft_body
    )
    meta[draft_path] = {"status": "draft", "document_type": "note"}
    template_of[draft_path] = "draft"

    # ---- T3: mixed bullets + prose + table, hard-wrapped 72 ----
    voice_gold = voice_items[0] if voice_items else ""
    spec_gold = spec_items[0] if spec_items else ""
    mixed = []
    mixed.append("## Product specification")
    mixed.append("")
    mixed.append(_hard_wrap(f"Our voice is {voice_gold}."))
    mixed.append("")
    mixed.append(_hard_wrap("First-time buyers aged 20-30 are the core audience for this launch."))
    mixed.append("")
    mixed.append("- Primary colour: mustard-yellow.")  # trap: descriptive mustard-yellow
    mixed.append("")
    mixed.append(_hard_wrap(_fill(rng, 10)))
    mixed.append("")
    mixed.append("| Attribute | Value |")
    mixed.append("|---|---|")
    mixed.append(f"| Material | {spec_gold} |")
    mixed.append("| Channel | Instagram is the primary channel |")
    mixed.append("")
    mixed.append(_hard_wrap(_fill(rng, 10)))
    mixed_body = _finalize("\n".join(mixed), rng, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    t3_path = f"{brand}/mixed.md"
    docs[t3_path] = (
        _frontmatter(
            {"title": "Product specification", "status": "approved", "document_type": "guide"}
        )
        + mixed_body
    )
    meta[t3_path] = {"status": "approved", "document_type": "guide"}
    template_of[t3_path] = "T3"

    # ---- Neutral filler: FILLER_DOC_COUNT docs, neutral names, no gold ----
    for i in range(FILLER_DOC_COUNT):
        name = f"doc-{i + 1:03d}.md"
        body = _finalize(_fill(rng, 55), rng, FILLER_MIN_WORDS, FILLER_MAX_WORDS)
        path = f"{brand}/{name}"
        docs[path] = (
            _frontmatter(
                {"title": f"Filler document {i + 1}", "status": "approved", "document_type": "note"}
            )
            + body
        )
        meta[path] = {"status": "approved", "document_type": "note"}
        template_of[path] = "filler"

    return {
        "brand": brand,
        "seed": seed,
        "docs": docs,
        "meta": meta,
        "template_of": template_of,
        "gold": {
            "standing_prohibitions": prohibitions,
            "required_voice_descriptors": voice,
            "approved_specs": specs_list,
            "superseded_v1_markers": superseded,
            "unapproved_draft_markers": drafts,
        },
    }


def write_fixtures(spec: dict, seed: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for brand in spec["brands"]:
        result = generate_brand(brand, spec, seed)
        bdir = out_dir / brand
        bdir.mkdir(parents=True, exist_ok=True)
        # Remove any stale files from previous runs so only current fixtures remain.
        for stale in bdir.glob("*.md"):
            stale.unlink()
        for path, content in result["docs"].items():
            fname = path.split("/", 1)[1]
            (bdir / fname).write_text(content, encoding="utf-8")
        (bdir / "_meta.json").write_text(
            json.dumps({"meta": result["meta"], "template_of": result["template_of"]}, indent=2),
            encoding="utf-8",
        )
        total_tokens = sum(_tokens(c) for c in result["docs"].values())
        summary[brand] = {
            "seed": seed,
            "doc_count": len(result["docs"]),
            "total_tokens": total_tokens,
            "approved_tokens": sum(
                _tokens(c)
                for p, c in result["docs"].items()
                if (result["meta"].get(p) or {}).get("status") == "approved"
            ),
        }
    return summary


if __name__ == "__main__":
    spec_path = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
    spec = json.loads(spec_path.read_text())
    dev_summary = write_fixtures(spec, DEV_SET_SEED, DEV_FIXTURE_DIR)
    sealed_summary = write_fixtures(spec, SEALED_SEED, SEALED_FIXTURE_DIR)
    print("dev_fixtures:", json.dumps(dev_summary, indent=2))
    print("sealed_fixtures:", json.dumps(sealed_summary, indent=2))
