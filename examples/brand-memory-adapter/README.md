# Brand Memory Adapter

SDK-only adapter for brand memory in a content-generation app. Use this adapter
when your application uses the Python SDK to reach Mind Palace over HTTP. For a
REST-only integration (no SDK import), see the reference example in
`examples/marketing-campaign-memory/`.

## Functions

- `load_documents(tenant, files)` — ingest each (path, content) pair into the
  tenant's corpus over HTTP.
- `context_for(tenant, campaign, budget_chars)` → (prompt_text, claim_ids,
  memory_available) — pack claim texts for generation in brand + campaign
  scope; returns `("", [], False)` if the memory service is unavailable.
- `record_decision(tenant, campaign, key, text, decision)` — record a reviewer
  verdict under the campaign scope; `decision` must be `"approved"` or
  `"rejected"` (anything else raises `ValueError`).
- `record_correction(tenant, claim_id, key, text)` — supersede a claim with
  corrected text, in the brand scope.

## Environment

- `MIND_PALACE_URL` — base URL of the Mind Palace server
- `BRAND_MEMORY_KEY` — bearer credential for the brand's corpus

All functions operate on the corpus named by the `tenant` argument, which must
match a corpus for which the credential holds a grant.
