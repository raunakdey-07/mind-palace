"""Prose vs structured extraction parity (M017 Phase 2)."""

from __future__ import annotations

from api.services import markdown_claims as mc
from api.services.parser import parse_markdown

STRUCTURED = """---
title: Northstar
---

# Northstar Sustainable Apparel

- Must avoid playful tones in all customer-facing copy.
- Use a calm, confident voice.
- Never make unsubstantiated sustainability claims.
"""

PROSE = """---
title: Northstar
---

# Northstar Sustainable Apparel

Northstar must avoid playful tones in all customer-facing copy.
We should use a calm, confident voice.
We must never make unsubstantiated sustainability claims.
"""


def test_prose_yields_core_constraints():
    _, b_s = parse_markdown(STRUCTURED)
    _, b_p = parse_markdown(PROSE)
    claims_s, _ = mc.extract(b_s, mc._chunks(b_s))
    claims_p, _ = mc.extract(b_p, mc._chunks(b_p))
    assert len(claims_s) >= 3
    assert len(claims_p) >= 3
    text_s = " ".join(c["claim"].lower() for c in claims_s)
    text_p = " ".join(c["claim"].lower() for c in claims_p)
    for needle in ("avoid playful", "calm, confident", "unsubstantiated"):
        assert needle in text_s
        assert needle in text_p
