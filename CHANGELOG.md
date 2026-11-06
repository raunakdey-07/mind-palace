## [Unreleased]
## [Unreleased]

### Prose extraction never truncates and no longer drops prose silently

A wrapped sentence is one claim, internal line break included, not a claim
cut at the line break. A document with no bullets at all is read for
rule-like sentences; `extract()` now returns a `prose_not_extracted` warning
for prose that produces no claim; previously it returned no warning. In a
document that has bullets, prose paragraphs are ignored and not reported;
put every rule and fact in a bullet. Ingest responses now carry `warnings`
(`prose_not_extracted` and the existing frontmatter warnings); responses
without warnings are unchanged. Prose limitation: paragraphs in a document
with bullets are not read and not reported, and without bullets only
sentences with words like must/never/always/should are read (#158).

### Draft/rejected/superseded claims withheld by default (v0.10.0)

`/api/memory/current` and `/api/memory/pack` now withhold claims whose
`info_state` is `draft`, `rejected` or `superseded`. history and query are
unchanged. Claim carries `info_state` (excluded when `approved`), and
`MemoryResponse` carries `withheld_counts` (present only when something was
withheld). The new request field `include_states` opts individual states back
in; an unknown state is `422 invalid_state`.

### Scope-token enforcement made loud (v0.10.0)

A server-profile deployment (`VERILORE_DEPLOYMENT=server`) now refuses to
start on a mismatch between `VERILORE_SCOPE_SECRET` and the database's
`scope_hmac_secret` (the startup refusal pre-existed on main; this release
adds the readiness check, the rotation-order text, and explicit fail-closed
behaviour when the probe is unavailable). The refusal names the cause and the
safe rotation order (DB first, then environment, brief outage) and never prints
any secret. `/health/ready` reports `not_ready` with `checks.scope_token =
"mismatch"` when the probe is unavailable or the token does not verify. Local
deployments are unaffected.

### Corpus creation over HTTP

`POST /api/corpora` now translates row-level-security violations (SQLSTATE
42501) to `403` `forbidden` instead of leaking a 500. Other `ProgrammingError`s
still propagate as 500. In server mode, corpora are created by the operator
with `verilore corpus create`; `POST /api/corpora` is not an operator path
for a brand-new corpus.

### RLS scope hoisting (migration 016)

The `verilore_tenant` row-level-security policy on every corpus table is now
written with `(SELECT mp_verified_scope_corpora())` inlined directly in the
`USING` / `WITH CHECK` expression instead of calling
`verilore_allowed_corpus()`. PostgreSQL cannot inline the wrapper function, so
migration 015's policy invoked `mp_verified_scope_corpora()` once per row; the
inlined form is planned as an InitPlan (evaluated once per query). Semantics are
unchanged; `verilore_allowed_corpus` and its grants are left intact. See
`tests/test_enforcement_e2e.py::test_latency_and_explain_initplan` for the
InitPlan / call-count proof (no per-row trap).

### `remember` records decisions: `state` and `scope`

`POST /api/memory/remember` accepts two new request fields. `state` is
"approved" (default), "draft" or "rejected" -- anything else is `422
invalid_state` -- and is stored as a `status:` line on the document plus
`info_state` on the authored claim, byte-identical to before when approved.
`scope` is an optional relative folder (e.g. `campaigns/spring`); the document is
stored at `f"{scope}/{memory_path(key)}"` and the same key in two scopes is two
memories, so they never supersede or conflict. Malformed scopes are `422
invalid_scope`. Validation lives in `check_state` / `check_scope`, which raise
`DecisionError` so the route keeps the error code; `invalid_state` and
`invalid_scope` flow through the SDK, the local runtime and the CLI (`--state`,
`--scope`) alike. With scope, the stored claim key is `f"{scope}/{key}"`; the
response's key is that full key, so sending it back as key with the same scope
is the same memory, and topic_key is scoped the same way.

### Supersession mistakes are 422, not 500

remember: supersession input errors (unknown topic_key, no or several matching
claims) are 422 invalid_request, not 500.

### path_prefix scoping

query, pack, current and history accept `path_prefix` (one string or a list; the
result is the union). `"campaigns/a"` never matches `"campaigns/ab"`. `path` and
`path_prefix` together are `422 path_conflict`. With `path_prefix`, a conflict
shows only its in-scope sides (one side left becomes current), and query ranks
only claims in scope. The SDK's `query`, `current`, `history`, `pack`, `recall`
and `explain` accept `path_prefix`.

### SDK bounded retry and backoff

The remote SDK now retries transient failures instead of failing the first time.
`Verilore(timeout=30, retries=2, backoff=0.2)` makes at most `retries + 1`
attempts, retrying only on `httpx.TimeoutException`, `httpx.RequestError` and
502/503/504 responses, sleeping `backoff * 2 ** (n - 1)` before retry `n`. A
timeout becomes `MemoryClientError("timeout", ..., 504)`, a transport error
`MemoryClientError("transport_error", ..., 503)`, both carrying `attempts`. All
four remote call sites go through one private `_send` helper. `retries=0` turns
retrying off; negative `retries`, `timeout <= 0` or negative `backoff` raise
`ValueError`. A retried `remember` is safe because identical arguments on the
same day return `UNCHANGED`, so a retry never writes a second version.

### MCP `memory_remember` accepts `state` and `scope`

The one MCP write tool now accepts `state` ("approved", "draft" or "rejected")
and `scope` (a relative folder such as "campaigns/spring"; with scope the stored
claim key is `"<scope>/<key>"`). Both are passed through to the SDK's remember,
so an agent can reject copy and file a decision under a campaign. `file=` cannot
be combined with `state` or `scope` and is refused with `invalid_request` before
the SDK is called; `DecisionError` keeps its code (`invalid_state`,
`invalid_scope`) instead of being reported as `invalid_evidence`.

### Integration guide and example adapter

New `docs/integration/guide.md`: wiring Mind Palace into a content-generation
app in one afternoon. Every python block runs in CI against a live server
(`tests/test_integration_guide_examples.py`). New `examples/brand-memory-adapter/brand_memory.py`:
SDK-only adapter (`load_documents`, `context_for`, `record_decision`,
`record_correction`). No LLM calls in tests; generation is simulated.

### History shows cross-document supersessions

`history(path=...)` and `changes(path=...)` now include superseded claims from
other documents in `changes[].previous` when the superseding claim's
`supersedes_id` points at them. The lineage filter keeps `after` claims that
are on the matched lineage first, then also keeps any `before` claim that is
the `supersedes_id` target of a kept `after` claim. Superseded siblings from
the same document still filter out, so no unrelated claim leaks.

### Tests

`tests/test_integration_brand_kit_scenario.py`: end-to-end brand-kit lifecycle over real HTTP
with scope-token enforcement on, reusing the shared `tests/live_server.py` fixture.


