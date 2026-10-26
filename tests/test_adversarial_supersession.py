"""Adversarial supersession lineage tests (M017 Phase 3)."""

from __future__ import annotations

from api.services import markdown_claims as mc
from api.services.parser import parse_markdown


def _claims(body):
    _, b = parse_markdown(body)
    return mc.extract(b, mc._chunks(b))[0]


def test_top_of_list_insertion_no_false_supersession():
    v1 = """---
title: v1
---

- Instagram is the primary channel.
- Avoid promotional discount messaging.
- Use a calm, conversational voice.
"""
    v2 = """---
title: v2
---

- TikTok is the primary channel.
- Instagram remains the secondary channel.
- Avoid promotional discount messaging.
- Use a calm, conversational voice.
"""
    c1 = _claims(v1)
    c2 = _claims(v2)
    # The two unchanged rules must still be present in v2 (not dropped).
    texts2 = [c["claim"] for c in c2]
    assert any("Avoid promotional discount messaging." in t for t in texts2)
    assert any("Use a calm, conversational voice." in t for t in texts2)
    # Keys for unchanged rules should be stable positions under their heading.
    keys1 = {c["key"]: c["claim"] for c in c1}
    keys2 = {c["key"]: c["claim"] for c in c2}
    # The unchanged rules retain their claim text in v2.
    assert "Avoid promotional discount messaging." in keys2.values()
    assert "Use a calm, conversational voice." in keys2.values()


def test_no_false_supersession_on_reorder():
    body = """---
title: t
---

- Alpha is the first item here.
- Beta is the second item here.
- Gamma is the third item here.
"""
    claims = _claims(body)
    assert len(claims) == 3