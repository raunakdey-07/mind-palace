# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""SDK-only adapter for brand memory in a content app.

No imports from `api.services`, no SQL, no model. The four functions are the
whole surface a FastAPI app needs to wire Mind Palace into generation:

- `load_documents` — ingest (path, content) pairs over HTTP
- `context_for` — pack claim texts for the prompt; degrades to no memory
- `record_decision` — remember a reviewer verdict under a campaign scope
- `record_correction` — supersede a claim, in the brand scope

Server URL and credential come from `MIND_PALACE_URL` and `BRAND_MEMORY_KEY`
environment variables, the same names the integration guide uses.
"""

from __future__ import annotations

import os

import httpx

from mindpalace_sdk import MemoryClientError, MindPalace


def _client(tenant: str) -> MindPalace:
    return MindPalace(
        tenant,
        base_url=os.environ["MIND_PALACE_URL"],
        api_key=os.environ["BRAND_MEMORY_KEY"],
        timeout=3,
        retries=1,
    )


def load_documents(tenant: str, files: list[tuple[str, str]]) -> None:
    """Ingest each (path, content) pair into the tenant's corpus over HTTP."""
    base_url = os.environ["MIND_PALACE_URL"]
    api_key = os.environ["BRAND_MEMORY_KEY"]
    for path, content in files:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/ingest/file",
            json={"content": content, "path": path, "corpus": tenant},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()


def context_for(
    tenant: str, campaign: str, budget_chars: int
) -> tuple[str, list[str], bool]:
    """Pack claim texts for generation in brand + campaign scope.

    Always packs with query="" so every current claim in scope is returned,
    rather than filtering by keyword relevance. Returns (prompt_text,
    claim_ids, memory_available). Prompt text is built from claim texts only,
    so the prompt stays small. On MemoryClientError, returns ("", [], False)
    so generation can proceed without memory.
    """
    mp = _client(tenant)
    try:
        pack = mp.memory.pack(
            query="",
            budget=budget_chars,
            path_prefix=["brand", f"campaigns/{campaign}"],
        )
    except MemoryClientError:
        return ("", [], False)
    texts = [c.claim for c in pack.current_memories]
    ids = [c.id for c in pack.current_memories]
    return ("\n".join(texts), ids, True)


def record_decision(tenant: str, campaign: str, key: str, text: str, decision: str) -> str:
    """Record a reviewer decision under the campaign scope.

    `decision` must be "approved" or "rejected"; anything else raises
    ValueError before any remote call. Returns the result event
    ("NEW" or "UNCHANGED").
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"decision must be 'approved' or 'rejected', got {decision!r}"
        )
    mp = _client(tenant)
    result = mp.memory.remember(
        statement=text,
        key=key,
        scope=f"campaigns/{campaign}",
        state=decision,
    )
    return result.event


def record_correction(tenant: str, claim_id: str, key: str, text: str) -> str:
    """Supersede a claim with corrected text, in the brand scope.

    Returns the result event ("NEW" or "UNCHANGED").
    """
    mp = _client(tenant)
    result = mp.memory.remember(
        statement=text,
        key=key,
        scope="brand",
        supersedes_claim_id=claim_id,
    )
    return result.event
