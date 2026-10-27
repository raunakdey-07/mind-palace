"""Draft checking against corpus rules."""

from __future__ import annotations

import re
import unicodedata

from api.services.corpora import get_corpus_by_name
from api.services.memory import _load


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.lower()
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def check_draft(db, corpus: str, draft_text: str) -> dict:
    corpus_row = await get_corpus_by_name(db, corpus)
    if corpus_row is None:
        raise ValueError("corpus not found")
    corpus_id = corpus_row["id"]

    versions = await _load(db, corpus_id)
    claims = []
    for v in versions:
        for c in v.get("claims", []):
            claims.append(c)

    # approved claims: info_state approved (default). superseded: info_state superseded or status superseded
    approved = [c for c in claims if (c.get("info_state") or "approved") == "approved"]
    superseded = [c for c in claims if (c.get("info_state") or "") == "superseded"]

    # banned terms from explicit quoted prohibitions
    banned = set()
    for c in approved:
        text = c.get("claim", "") or ""
        # straight and curly quotes
        for m in re.finditer(r'do not use the words\s*[:\-]?\s*([^\n]+)', text, re.IGNORECASE):
            segment = m.group(1)
            for q in re.findall(r'[""\u201c\u201d]([^""\u201c\u201d]+)[""\u201c\u201d]', segment):
                banned.add(q.strip())
        for m in re.finditer(r'prohibited\s*[:\-]\s*([^\n]+)', text, re.IGNORECASE):
            segment = m.group(1)
            for q in re.findall(r'[""\u201c\u201d]([^""\u201c\u201d]+)[""\u201c\u201d]', segment):
                banned.add(q.strip())
            # also comma separated after prohibited:
            for part in segment.split(","):
                part = part.strip().strip("\"'\u201c\u201d")
                if part and not part.startswith('"'):
                    pass
    # rejected items: their claim text as banned? Add as rejected_direction not term.
    rejected_items = [c for c in claims if (c.get("info_state") or "") == "rejected"]

    norm_draft = _normalize(draft_text)

    violations = []
    checked = 0
    unchecked = 0

    # prohibited terms
    for term in banned:
        if _normalize(term) in norm_draft:
            violations.append({"type": "prohibited_term", "matched": term, "rule": term})
    if banned:
        checked += len(banned)

    # rejected directions: if draft contains a rejected claim's key phrase? skip mechanical
    for rc in rejected_items:
        unchecked += 0

    # superseded_fact: values present in superseded claim and absent from approved
    approved_texts = " ".join(c.get("claim", "") for c in approved)
    norm_approved = _normalize(approved_texts)
    for sc in superseded:
        # extract numbers/values? Just check if whole claim text appears in draft and not approved
        sc_norm = _normalize(sc.get("claim", ""))
        if sc_norm and sc_norm in norm_draft and sc_norm not in norm_approved:
            violations.append({"type": "superseded_fact", "matched": sc.get("claim"), "rule": sc.get("claim")})

    # numbers/percentages/units in draft
    for m in re.finditer(r'\d+(?:\.\d+)?\s*(?:%|percent|mm|cm|mg|kg|s|sec|seconds)?', draft_text):
        num = m.group(0).strip()
        if len(num) < 2:
            continue
        if _normalize(num) not in norm_approved:
            # only flag if not present
            violations.append({"type": "unsupported_number", "matched": num, "span": m.group(0)})
    # dedupe unsupported numbers maybe keep

    # subjective rules count
    for c in approved:
        low = (c.get("claim", "") or "").lower()
        if any(k in low for k in ("tone", "voice", "style", "warm", "dry")):
            unchecked += 1
        else:
            # mechanical-ish
            pass

    # checked rules count: approved rule claims
    checked_rules_count = len([c for c in approved if any(k in (c.get("claim","")).lower() for k in ("must","never","avoid","prohibited","should","always","cannot","do not"))])
    unchecked_rules_count = unchecked if unchecked else max(0, len(approved) - checked_rules_count)

    total = checked_rules_count + unchecked_rules_count
    coverage = (checked_rules_count / total) if total > 0 else None

    verdict = "violations" if violations else "no_violations_found"

    return {
        "verdict": verdict,
        "violations": violations,
        "checked_rules": checked_rules_count,
        "unchecked_rules": unchecked_rules_count,
        "mechanical_coverage": coverage,
    }
