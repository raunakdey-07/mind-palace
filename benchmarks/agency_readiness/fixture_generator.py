"""Deterministic fixture generator for the M018 sealed benchmark.

Builds each brand's corpus from the sealed spec's gold data. Fixed seed,
no Mind Palace imports, no tuning to Mind Palace vocabulary.

Two output sets are produced:
  * dev_fixtures/   -- dev seed (DEV_SET_SEED), filler variation, for iteration
                    and the smoke test. NOT used by the sealed baseline run.
  * sealed_fixtures/-- sealed seed (SEALED_SEED), used by the sealed baseline run.

Every document carries a required `title` frontmatter field so the product's
structural prose extractor runs (missing title -> frontmatter_missing_title
warning -> extraction skipped -> zero claims).
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

# Brand-neutral filler templates (contain none of the gold strings).
FILLER_TEMPLATES = [
    "The product ships with a two-year limited warranty covering defects in materials and workmanship.",
    "Customer support is available by email and phone during business hours, Monday to Friday.",
    "Orders are processed within one business day and shipped with tracking information.",
    "Returns are accepted within thirty days of purchase with original packaging and receipt.",
    "The maintenance schedule recommends inspection every six months for optimal performance.",
    "Software updates are delivered automatically and require a restart to take effect.",
    "Accessibility features include high-contrast mode, keyboard navigation, and screen-reader support.",
    "The mobile application syncs across devices using end-to-end encryption.",
    "Battery life depends on usage patterns and typically lasts a full day on a single charge.",
    "Installation requires a Phillips-head screwdriver and approximately twenty minutes.",
    "The specifications table lists dimensions, weight, power draw, and operating temperature.",
    "User feedback is collected through surveys and used to prioritize the roadmap.",
    "Pricing varies by region and volume, with discounts for educational and nonprofit customers.",
    "The compliance program addresses data retention, audit trails, and access controls.",
    "Training materials include videos, quick-reference guides, and live webinars.",
]

PRODUCT_PAGES = [
    "Our product line spans entry-level, mid-range, and professional configurations.",
    "Each configuration is tested against the same quality standard before release.",
    "Compatibility charts show which accessories work with which model variant.",
    "The technical datasheet lists every certified component and its tolerance.",
]

FAQS = [
    "What is the warranty period? It is two years from the date of purchase.",
    "How long does shipping take? Most orders arrive within three to five business days.",
    "Can I return an opened item? Yes, within thirty days with the original receipt.",
    "Do you offer bulk pricing? Contact sales for volume and nonprofit discounts.",
]

MEETING_NOTES = [
    "The team reviewed the quarterly roadmap and agreed on three priority initiatives.",
    "Action items were assigned to engineering, design, and customer success.",
    "The next review is scheduled for the first Tuesday of the following month.",
]


def _rng(seed: int, brand: str) -> random.Random:
    return random.Random(f"{seed}:{brand}")


def _fill(rng, n: int) -> str:
    sentences = list(FILLER_TEMPLATES + PRODUCT_PAGES + FAQS + MEETING_NOTES)
    rng.shuffle(sentences)
    return " ".join(sentences[:n])


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

    # ---- T3: mixed content, hard-wrapped 72, table, voice + specs ----
    mixed = []
    mixed.append("## Product specification")
    mixed.append("")
    mixed.append(_hard_wrap("Our voice is warm, a little dry, and confident."))
    mixed.append("")
    mixed.append(_hard_wrap("First-time buyers aged 20-30 are the core audience for this launch."))
    mixed.append("")
    mixed.append("| Attribute | Value |")
    mixed.append("|---|---|")
    mixed.append("| Material | 80% GRS-certified recycled polyester |")
    mixed.append("| Channel | Instagram is the primary channel |")
    mixed.append("")
    mixed.append(_hard_wrap(_fill(rng, 12)))
    t3_path = f"{brand}/t3_mixed.md"
    docs[t3_path] = _frontmatter({"title": "Product specification", "status": "approved", "document_type": "guide"}) + "\n".join(mixed)
    meta[t3_path] = {"status": "approved", "document_type": "guide"}
    template_of[t3_path] = "T3"

    # ---- T1: state conveyed by headings (vocabulary + non-vocabulary) ----
    t1 = []
    t1.append("## Current guidance")
    t1.append("")
    t1.append(_hard_wrap('Do not use the words "curated" or "bespoke."'))
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 8)))
    t1.append("")
    t1.append("## Brainstorming")
    t1.append("")
    t1.append(_hard_wrap("Try a playful tone again."))
    t1.append("")
    t1.append(_hard_wrap("50% flash sale countdown."))
    t1.append("")
    t1.append("## Ideas parking lot")
    t1.append("")
    t1.append(_hard_wrap("TikTok meme giveaway."))
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 6)))
    t1.append("")
    t1.append("## Superseded")
    t1.append("")
    t1.append(_hard_wrap("65% recycled polyester was the old target."))
    t1.append("")
    t1.append(_hard_wrap(_fill(rng, 6)))
    t1_path = f"{brand}/t1_headings.md"
    docs[t1_path] = _frontmatter({"title": "Current guidance", "status": "approved", "document_type": "guide"}) + "\n".join(t1)
    meta[t1_path] = {"status": "approved", "document_type": "guide"}
    template_of[t1_path] = "T1"

    # ---- T2: state via frontmatter status; v2 (approved) on disk ----
    v2 = []
    v2.append("## Current policy")
    v2.append("")
    v2.append('Must never use the word "curated" in public copy.')  # rule inserted at position 1
    v2.append("")
    v2.append(_hard_wrap("Instagram is the primary channel for launches."))
    v2.append("")
    v2.append(_hard_wrap(_fill(rng, 6)))
    t2_path = f"{brand}/t2_policy.md"
    docs[t2_path] = _frontmatter({"title": "Current policy", "status": "approved", "document_type": "policy"}) + "\n".join(v2)
    meta[t2_path] = {"status": "approved", "document_type": "policy"}
    template_of[t2_path] = "T2"

    # ---- Draft file ----
    draft_content = []
    draft_content.append("## Brainstorming")
    draft_content.append("")
    draft_content.append(_hard_wrap("We draft emails every Monday."))
    draft_content.append("")
    draft_content.append(_hard_wrap("Overnight muscle gain results are expected within weeks."))
    draft_content.append("")
    draft_content.append(_hard_wrap(_fill(rng, 6)))
    draft_path = f"{brand}/september_brainstorm.md"
    docs[draft_path] = _frontmatter({"title": "September brainstorm", "status": "draft", "document_type": "note"}) + "\n".join(draft_content)
    meta[draft_path] = {"status": "draft", "document_type": "note"}
    template_of[draft_path] = "T1"

    # ---- Volume: 6000+ tokens of current approved material (filler, sorts first) ----
    acc = ""
    while _tokens(acc) < 6000:
        acc += " " + _fill(rng, 20)
    volume = []
    volume.append("## Volume guide")
    volume.append("")
    volume.append(_hard_wrap(acc.strip()))
    vol_path = f"{brand}/00_volume_guide.md"  # sorts first so filler leads the concatenation
    docs[vol_path] = _frontmatter({"title": "Volume guide", "status": "approved", "document_type": "guide"}) + "\n".join(volume)
    meta[vol_path] = {"status": "approved", "document_type": "guide"}
    template_of[vol_path] = "volume"

    # Prohibition docs spread across files with cues
    for i, p in enumerate(prohibitions):
        content = []
        content.append(f"## Prohibited vocabulary ({i + 1})")
        content.append("")
        content.append(_hard_wrap(f'Do not use the word "{p}" in any customer-facing copy.'))
        content.append("")
        content.append(_hard_wrap(_fill(rng, 5)))
        path = f"{brand}/prohibition_{i}.md"
        docs[path] = _frontmatter({"title": f"Prohibited vocabulary {i + 1}", "status": "approved", "document_type": "styleguide"}) + "\n".join(content)
        meta[path] = {"status": "approved", "document_type": "styleguide"}
        template_of[path] = "prohibition"

    # Voice docs
    for i, v in enumerate(voice):
        content = []
        content.append(f"## Voice descriptor {i + 1}")
        content.append("")
        content.append(_hard_wrap(f"Our voice is {v}."))
        content.append("")
        content.append(_hard_wrap(_fill(rng, 5)))
        path = f"{brand}/voice_{i}.md"
        docs[path] = _frontmatter({"title": f"Voice descriptor {i + 1}", "status": "approved", "document_type": "styleguide"}) + "\n".join(content)
        meta[path] = {"status": "approved", "document_type": "styleguide"}
        template_of[path] = "voice"

    # Spec docs
    for i, s in enumerate(specs_list):
        content = []
        content.append(f"## Approved specification {i + 1}")
        content.append("")
        content.append(_hard_wrap(f"The material is {s}."))
        content.append("")
        content.append(_hard_wrap(_fill(rng, 5)))
        path = f"{brand}/spec_{i}.md"
        docs[path] = _frontmatter({"title": f"Approved specification {i + 1}", "status": "approved", "document_type": "spec"}) + "\n".join(content)
        meta[path] = {"status": "approved", "document_type": "spec"}
        template_of[path] = "spec"

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
        for path, content in result["docs"].items():
            fname = path.split("/", 1)[1]
            (bdir / fname).write_text(content, encoding="utf-8")
        (bdir / "_meta.json").write_text(
            json.dumps({"meta": result["meta"], "template_of": result["template_of"]}, indent=2), encoding="utf-8"
        )
        total_tokens = sum(_tokens(c) for c in result["docs"].values())
        summary[brand] = {
            "seed": seed,
            "doc_count": len(result["docs"]),
            "total_tokens": total_tokens,
            "approved_tokens": sum(_tokens(c) for p, c in result["docs"].items() if (result["meta"].get(p) or {}).get("status") == "approved"),
        }
    return summary


if __name__ == "__main__":
    import sys

    spec_path = ROOT / "benchmarks" / "agency_readiness" / "sealed_spec.json"
    spec = json.loads(spec_path.read_text())
    dev_summary = write_fixtures(spec, DEV_SET_SEED, DEV_FIXTURE_DIR)
    sealed_summary = write_fixtures(spec, SEALED_SEED, SEALED_FIXTURE_DIR)
    print("dev_fixtures:", json.dumps(dev_summary, indent=2))
    print("sealed_fixtures:", json.dumps(sealed_summary, indent=2))
