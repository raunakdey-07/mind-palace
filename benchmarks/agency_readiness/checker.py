"""Scoring only. No Mind Palace imports."""

from __future__ import annotations

import unicodedata
import re


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    # drop apostrophes and quotes
    text = (
        text.replace("'", "")
        .replace("\u2019", "")
        .replace('"', "")
        .replace("\u201c", "")
        .replace("\u201d", "")
    )
    # replace every other non-letter/digit/% with space
    out = []
    for ch in text:
        if ch.isalnum() or ch == "%":
            out.append(ch)
        else:
            out.append(" ")
    text = "".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def present(gold: str, context: str) -> bool:
    ng = " " + normalize(gold) + " "
    nc = " " + normalize(context) + " "
    return ng in nc


def score(context: str, gold: dict) -> dict:
    # gold: brand spec dict
    prohibitions = gold.get("standing_prohibitions", [])
    voice = gold.get("required_voice_descriptors", [])
    specs = gold.get("approved_specs", [])
    superseded = gold.get("superseded_v1_markers", [])
    drafts = gold.get("unapproved_draft_markers", [])

    rule_recall = (
        (sum(1 for p in prohibitions if present(p, context)) / len(prohibitions))
        if prohibitions
        else 1.0
    )
    voice_recall = (sum(1 for v in voice if present(v, context)) / len(voice)) if voice else 1.0
    spec_recall = (sum(1 for s in specs if present(s, context)) / len(specs)) if specs else 1.0
    violation = 1 if any(present(m, context) for m in (superseded + drafts)) else 0
    stale_hits = sum(1 for m in (superseded + drafts) if present(m, context))

    return {
        "rule_recall": rule_recall,
        "voice_recall": voice_recall,
        "spec_recall": spec_recall,
        "violation": violation,
        "stale_hits": stale_hits,
    }
