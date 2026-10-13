# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""A deterministic stand-in for a content model.

**This is simulated.** No model is called, no network is used, and no paid
service is involved. The point of the example is the memory boundary, and a real
model would make the security assertions depend on a network.

The stand-in is deliberately dull rather than clever, because a generator that
could invent anything would let the example's claims about evidence drift
quietly. It composes text only from facts it is handed, and it says which fact
went where, so a test can assert that a claim without an approved source never
appears in an output.

The interface is the one a real generator would have: approved brand facts, an
approved campaign brief, and a platform. It returns text plus the provenance of
every sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PLATFORMS = ("blog", "linkedin", "facebook", "instagram")

#: Per-platform shape, so the four variants differ visibly without a model.
_SHAPE: dict[str, tuple[str, str]] = {
    "blog": ("{hook}\n\n{body}\n\n{sources}"),
    "linkedin": ("{hook} -- {body} ({sources})"),
    "facebook": ("{hook} {body}"),
    "instagram": ("{hook} | {body}"),
}


@dataclass(frozen=True, order=True)
class Fact:
    """One approved fact, with the memory version it came from."""

    key: str
    text: str
    version_id: str | None = None


@dataclass
class Draft:
    """A composed asset.

    ``claims_text`` is the part that makes assertions; ``body`` is that plus the
    provenance footer. They are separate because a provenance list is not a
    claim, and a checker that scanned the footer would report it as one.
    """

    body: str
    claims_text: str = ""
    used: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def generate(
    platform: str,
    brand_facts: list[Fact],
    campaign_facts: list[Fact],
    brief: str,
) -> Draft:
    """Compose one asset from approved facts only.

    Anything the caller wants to say that is not backed by a supplied fact is
    reported in `unsupported` rather than written into the body. That is the
    contract a real generator has to be held to as well, and it is what the
    example's tests assert about unapproved claims.
    """
    if platform not in _SHAPE:
        raise ValueError(f"unsupported platform: {platform}")

    supplied = {fact.key for fact in brand_facts} | {fact.key for fact in campaign_facts}
    hook = campaign_facts[0].text if campaign_facts else brief
    body_parts = [fact.text for fact in brand_facts]
    body_parts.extend(fact.text for fact in campaign_facts[1:])

    used = sorted(supplied)
    template = _SHAPE[platform]
    claims_text = template.format(hook=hook, body=" ".join(body_parts), sources="")
    text_out = template.format(
        hook=hook,
        body=" ".join(body_parts),
        sources=", ".join(used) or "no approved sources",
    )
    return Draft(body=text_out, claims_text=claims_text, used=used)


def unsupported_claims(draft: Draft, facts) -> list[str]:
    """Sentences in a draft that no approved fact supports.

    `facts` may be `Fact` objects or their text; either way it is the *approved*
    set, which is what decides support. Used by the example to show that an
    unapproved claim stays out of the published asset rather than quietly riding
    along in it.
    """
    known = [getattr(fact, "text", fact) for fact in facts]
    # A stored `Asset` and a fresh `Draft` are both accepted: the service keeps
    # the draft as an asset, and the audit question is the same for both. The
    # assertion-bearing text is read under either of its two names, so a
    # provenance footer is never mistaken for an unsupported claim.
    claims_text = (
        getattr(draft, "claims_text", None)
        or getattr(draft, "claims", None)
        or getattr(draft, "body", "")
    )
    sentences = [part.strip() for part in claims_text.replace("|", ".").split(".") if part.strip()]
    return [s for s in sentences if s and not any(s in approved for approved in known)]
