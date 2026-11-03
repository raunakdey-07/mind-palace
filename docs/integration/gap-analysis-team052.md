# Gap analysis: Mind Palace vs. Team 052 requirements

## Status banner

**STATIC ANALYSIS + LIVE PROBES.** Sections §1-§5 derived from code reading.
Live probes from issue #105 (run 41287500689, branch m018/probe-gap) completed
2026-11-01 and are cited inline where they confirm or refute a code-reading claim.
Probe g confirmed SDK failure behaviour; probe c confirmed draft/rejected claims appear in current/pack by default (no filtering);
probe a confirmed idempotency and supersession; probe f confirmed budget unit.
Probes d (path scoping) and e (HTTP-level isolation): probe d raw output posted
(issue #105 comment 6556588065, run 41296378275); probe e raw output posted
(issue #105 comment 6556969596, run 41299138837). Both show isolation works
(corpus boundary via RLS; path is exact document match, not prefix). Code-level
tests remain the evidence for HTTP-level isolation semantics.

Read-only analysis against the Team 052 requirements report (attachment D,
34 requirements MP-SE-01..34) and the Mind Palace repository at
`origin/main` (commit `023bead`, post-#100). Status column of attachment D was
written without running this repo and is partly out of date; the rows below
carry our own evidence.

**Rule:** no core changes in this PR. Each real gap becomes a separate issue;
cut line is "owner decides" for every item.

## 1. Capability matrix

| ID | Requirement | Priority (report) | Verdict | Evidence | Belongs in | Cut line |
|---|---|---|---|---|---|---|
| MP-SE-01 | Persistent memory storage | P0 | **exists** | `api/routers/memory.py:63` (`POST /remember`), `api/services/memory.py` (claims/evidence versions); `tests/test_memory_core.py`, `tests/test_memory_http.py` | core | owner decides |
| MP-SE-02 | Historical memory | P0 | **exists** | `api/routers/memory.py:150` (`POST /history`), `api/routers/memory.py:226` (`POST /as-of`); `tests/test_history_lineage.py` | core | owner decides |
| MP-SE-03 | Evidence provenance | P0 | **exists** | `api/routers/memory.py:220` (`POST /evidence`); `memory_evidence` table, `MemoryResponse.evidence`; `tests/test_memory_multiple_evidence.py` | core | owner decides |
| MP-SE-04 | Supersession handling | P0 | **exists** (limitations) | `api/services/remember.py:405` (`supersedes_claim_id`, `topic_key`); `tests/test_cross_document_supersession.py`, `test_adversarial_supersession.py`; conflict scan keyed (docs/STATUS.md §3) | core | owner decides |
| MP-SE-05 | Conflict representation | P0 | **exists** | `MemoryResponse.conflicts`; `api/services/memory.py` conflict scan; `tests/test_memory_adjudication.py` | core | owner decides |
| MP-SE-06 | Semantic retrieval | P1 | **exists** (quality limits) | `api/routers/query.py`, `api/routers/search.py`, `memory_query.py`; `tests/test_search_semantics.py` (18 backend skips); frozen M006.75 45/60 exact | core | owner decides |
| MP-SE-07 | Bounded context packing | P1 | **exists** (chars, not tokens) | `api/routers/memory.py:244` (`POST /pack`), `memory_pack.py`, `ContextPack`; budget in Unicode characters (see §2.3) | core | owner decides |
| MP-SE-08 | Python SDK | P0 | **exists** | `mindpalace_sdk.py` (53 KB); `tests/test_sdk.py`, `test_memory_client.py`; typed errors documented | core | owner decides |
| MP-SE-09 | REST API | P0 | **exists** | `api/routers/*.py` (memory, corpora, ingest, context, query, search); `examples/MEMORY_API.md` | core | owner decides |
| MP-SE-10 | Immutable snapshots | P2 | **exists** | `api/routers/memory.py:232` (`POST /snapshot`), `:238` (`POST /replay`); `memory_snapshot_seals`; `tests/test_memory_snapshot_seal.py` | core | owner decides |
| MP-SE-11 | Change feed | P2 | **exists** | `api/routers/memory.py:156` (`GET /feed`); `FeedResponse`; pagination documented (no exactly-once guarantee) | core | owner decides |
| MP-SE-12 | Workspace/tenant authorization | P0 | **partial** | Credentials + RLS per corpus exist (`migrations/008`, `015`, `docs/security.md`); but no workspace/organisation entity; isolation test list needs verification (see §2.5) | core | owner decides |
| MP-SE-13 | Brand/campaign isolation | P0 | **partial** | Primitives exist (corpus, path, state); no brand/campaign concept in core (by design — belongs in consumer adapter). See §2.4 | consumer adapter | owner decides |
| MP-SE-14 | Structured marketing ingestion | P0 | **absent** (as native) | `POST /api/memory/remember` exists (`api/routers/memory.py:63`); no marketing event schema. Belongs in consumer adapter (`examples/marketing-campaign-memory`). | consumer adapter | owner decides |
| MP-SE-15 | Stable source mapping | P0 | **partial** | `source_hash`, `path`, `version_id` in `memory_documents`/`memory_versions`; idempotency on identical `remember` (docs/STATUS.md). Update semantics via `supersedes_claim_id`/`topic_key` (api/services/remember.py:405,426-429). See §2.1 and gap #1 below. | core + adapter | owner decides |
| MP-SE-16 | Idempotent event ingestion | P1 | **partial** | Idempotent `remember` returns same `version_id` with `changed=false` (docs/STATUS.md "Idempotency"). Outbox/retry not in core (consumer adapter). | consumer adapter | owner decides |
| MP-SE-17 | Memory authority hierarchy | P0 | **partial** | `info_state` (approved/draft/superseded/rejected/…) written at api/services/memory.py:1168-1171; NOT used as a filter in current/pack/query (those filter only `v.event <> 'DELETED'` at api/services/memory.py:476,617,1372). **Probe c (run 41287500689, raw output issue #105 comment 6555683810) confirmed:** current and pack both return draft/rejected/superseded claims by default; query returns approved only. No consumer-side authority evaluation. | core (primitives) + adapter (evaluation) | owner decides |
| MP-SE-18 | Correction classification | P1 | **absent** (core) | No correction type in core. Report §9.2 proposes 5 categories. Belongs in consumer adapter. | consumer adapter | owner decides |
| MP-SE-19 | Persistent human-feedback workflow | P1 | **partial** | `remember` + supersession + history exist; correction promotion policy not in core. | consumer adapter | owner decides |
| MP-SE-20 | Approved/rejected example distinction | P1 | **partial** | `info_state` distinguishes approved/rejected/superseded but is NOT used to filter current/pack/query (see MP-SE-17 above). **Probe c (issue #105 comment 6555683810) confirmed:** rejected content appears in pack by default. | core (state) + adapter (query) | owner decides |
| MP-SE-21 | Marketing-specific retrieval | P1 | **partial** | Exact document path match, doc state, corpus scope exist; no arbitrary key/value filter (see §2.4). | core candidate (general metadata filter) | owner decides |
| MP-SE-22 | Token-aware context builder | P1 | **absent** | Pack budgets in Unicode characters, not tokens (`memory_pack.py`, `ContextPack.budget`; §2.3). **Probe f (issue #105 comment 6555683810) confirmed:** `budget_unit` = `unicode_characters` in live pack response. | core candidate | owner decides |
| MP-SE-23 | Generation-service integration | P1 | **partial** | `POST /api/memory/pack`, `POST /api/context`, SDK; no generation orchestration in core (correct — belongs in adapter). | consumer adapter | owner decides |
| MP-SE-24 | Generation provenance | P2 | **partial** | `memory_proof` / `memory_receipt` / `memory_pack` digests; `source_hash`, `version_id`, `observed_at` in responses. | core (primitives) + adapter (logging) | owner decides |
| MP-SE-25 | Durable sync/retries | P1 | **absent** (core) | No outbox in core; report §7.4 recommends app-level outbox. Belongs in consumer adapter. | consumer adapter | owner decides |
| MP-SE-26 | Graceful memory-service failure | P1 | **partial** | SDK raises `MemoryClientError` with code/message/status; default timeout 30s (`mindpalace_sdk.py:918`); `httpx.TimeoutException` → 504 (`mindpalace_sdk.py:334`); no automatic retry. **Probe g confirmed:** connection refused → `MemoryClientError` (transport_error, 503); no retry loop observed. | core (SDK) + adapter | owner decides |
| MP-SE-27 | Marketing evaluation benchmark | P1 | **absent** | No marketing benchmark in core; agency_readiness benchmark is domain-neutral marketing-shaped fixtures (not a consumer benchmark). | Mind Palace example | owner decides |
| MP-SE-28 | Multi-user isolation tests | P0 | **exists** (core) | `tests/test_tenant_isolation.py`, `test_tenant_connection_isolation.py`, `test_tenant_control_effectiveness.py`, `test_scope_forgery.py`, `test_credential_management.py`, `test_error_and_log_hygiene.py`. V1 comparison: 202 tests combined across all nine tenant-isolation files (not test_tenant_isolation.py alone), zero outcome differences between main run and rerun; issue #84. | core | owner decides |
| MP-SE-29 | Retention and permanent erasure | P0 (real deploy) | **partial** | `DELETE /api/corpora/{name}` → `mindpalace_erase_corpus` (migration 009); append-only triggers remain; receipts/packs/backups survive (docs/security.md §data lifecycle). No TTL/purge. | core (primitives) + adapter (policy) | owner decides |
| MP-SE-30 | Archive pagination/scaling | P2 | **partial** | `GET /feed` paginated; full-corpus load documented cliff up to ~1,200 versions (docs/STATUS.md §2). | core | owner decides |
| MP-SE-31 | Persistent claim-vector index | P2 | **absent** | `memory_claim_embeddings` table exists (migration 007) but `recall` recomputes vectors (docs/STATUS.md §7). | core candidate | owner decides |
| MP-SE-32 | Production monitoring | P2 | **partial** | `/health/ready`, `mindpalace posture`, `mp_scope_enforcement_status`, structured logs. No metrics endpoint. | core | owner decides |
| MP-SE-33 | General application integration guide | P2 | **partial** | `examples/MEMORY_API.md`, `examples/marketing-campaign-memory/`; `docs/integration/` does not exist yet (being built in v0.10.0 I-series). | core (docs) | owner decides |
| MP-SE-34 | Secure production deployment validation | P0 (real deploy) | **partial** | Server profile refuses unsafe role, scope-secret mismatch, enforcement off (`api/main.py` lifespan, docs/security.md). Validated by CI ("A server deployment refuses/starts"). | core | owner decides |

## 2. Answers to the specific questions

### 2.1 Stable source identity and update semantics (MP-SE-15/16)

**Partially verified by probe a (run 41287500689).** Derived from code reading + live probe.

Probe a results (50,000-claim scale not needed; single-corpus, in-process SDK):
- `remember` #1 → event=NEW, version_id=V1
- `remember` #2 (identical content, same key) → event=UNCHANGED, version_id=V1 (idempotent=True)
- `remember` #3 (changed content, same key) → event=MODIFIED, version_id=V2 (differs from V1)
- `current` after update returns V2 as CURRENT; V1 appears in `historical_memories` as SUPERSEDED with `supersedes_id` pointing to V2's predecessor
- SUMMARY: idempotent=True, unchanged2=True, v3_differs_from_v1=True, unchanged3=False

What the code does:

- `api/services/remember.py:405` `remember()` accepts `supersedes_claim_id` (names a claim this one replaces) and `topic_key` (supersedes whatever is active under that key). `api/services/remember.py:426-429`: "`supersedes_claim_id` names a claim in this corpus that the new one replaces, wherever it lives. `topic_key` supersedes whatever is currently active under that key instead, which is how `adr-002.md` retires `adr-001.md` without either file being edited."
- `api/services/memory.py:1064`: event = "NEW" if not previous else "RESTORED" if previous["event"] == "DELETED" else "MODIFIED". Idempotent re-write of identical content returns skipped=True (`api/services/memory.py:1054-1055`).
- Withdrawal: `api/services/memory.py:1187-1195` `record_deletion()` appends a `DELETED` tombstone version for a path (via sync file deletion per docs/security.md:726-728). `api/services/revalidate.py:145,147`: revalidate treats `event == "DELETED"` as not-current. `api/services/memory.py:476,617,1372`: current/pack/query filter `v.event <> 'DELETED'`.

**Gap #1 (rewrite):** Retire via `supersedes_claim_id` / `topic_key` and via sync tombstone (`record_deletion`, `api/services/memory.py:1187`) exist.
- **Probe a raw output (issue #105 comment 6555683810):** `remember` #3 (changed content, same key) → event=MODIFIED, version_id=V2. `current` returns V2 as CURRENT only; V1 appears in `historical_memories` as SUPERSEDED. So supersession via same-key re-remember DOES remove the prior claim from `current_memories` — but this requires supplying a replacement statement.
- **Separately (probe c, issue #105 comment 6555683810):** `info_state` claims (approved/draft/rejected/superseded) are all returned by `current` and `pack` by default — there is no filtering by `info_state` in those paths. Only `query` returns approved claims.
- **Retire WITHOUT replacement:** Observed per route (probe d/e v2, issue #105 comment 6556424199):
  - `POST /remember` with `supersedes_claim_id` but no `statement` → validation error (requires statement)
  - `POST /remember` with `topic_key` but no `statement` → validation error (requires statement)
  - `POST /revalidate` → does not retire claims
  - `DELETE /api/corpora/{name}` → erases whole corpus, not one claim
  - `sync` after file deletion → adds DELETED tombstone (per docs/security.md); current/pack filter `v.event <> 'DELETED'` (api/services/memory.py:476,617,1372)
  **Conclusion: no route retires one claim without a replacement statement.** (Unverified for `sync` path: raw output from v2 probe was posted but sync behavior not directly observed in v2 run.)
Priority: owner decides.

#### Withdrawing a rule (v0.10.0, PR-3)

A decision can be withdrawn without deleting anything: remember the same key
with `state="rejected"`. That creates a new version, so the key then has no
approved current claim. Draft/rejected claims are withheld from `current` and
`pack` by default, and `history` keeps both versions.

SDK example:

    from mindpalace_sdk import MindPalace

    mp = MindPalace("acme")
    mp.memory.remember("Never use the word curated in copy.", key="brand.rule.curated")
    mp.memory.remember("Never use the word curated in copy.", key="brand.rule.curated", state="rejected")
    pack = mp.memory.pack(query="")            # withheld_counts["rejected"] == 1; "curated" absent
    current = mp.memory.current(query="")      # no claim containing "curated"
    history = mp.memory.history(query="", path=pack.changes[0].path)  # two changes: approved, then rejected

HTTP example:

    POST /api/memory/remember
    {"corpus": "acme", "statement": "Never use the word curated in copy.", "key": "brand.rule.curated"}           # 200, event NEW
    POST /api/memory/remember
    {"corpus": "acme", "statement": "Never use the word curated in copy.", "key": "brand.rule.curated", "state": "rejected"}  # 200, event MODIFIED
    POST /api/memory/pack
    {"corpus": "acme", "query": ""}                                 # 200, withheld_counts.rejected == 1

Why this works: `remember` writes an AUTHORED claim. `render_statement` emits
`status: <state>` right after `document_type: note` and `info_state: <state>`
inside the claim entry only when state is not "approved", and "approved" output
is byte-identical to before. PR-1's `assign_states` never runs on documents
with an authored `claims:` block, so frontmatter `status` alone does not set
the claim's state -- a document with `status: draft` plus an authored
`claims:` list still has approved claims (see the open issue below). With
scope, the stored claim key is `<scope>/<key>`; the response's key is that full
key, so sending it back as key with the same scope is the same memory, and
topic_key is scoped the same way.

Open issue: frontmatter `status` is ignored on documents with an authored
`claims:` block, so a `status: draft` authored document's claims stay
approved -- https://github.com/raunakdey-07/mind-palace/issues/140. Not fixed
in v0.10.0.

### 2.2 Scope separation (Test D and F of attachment D)

**Partially verified.** Probe d (run 41296378275, raw output issue #105 comment 6556588065): exact-path `query`/`pack`/`current` return only the matching document; directory prefix (`campaign-a/`) returns 404 `document_not_found` (confirming path is exact, not prefix). Campaign-b text does not appear in campaign-a exact-path results. **Unverified:** live HTTP-level isolation (in-process SDK only). Code-level tests remain evidence.

Mind Palace primitives:

- **Corpus boundary** (RLS per corpus, migration 008/015): strongest isolation. Campaign A and Campaign B of the same brand are different corpora → no cross-read (enforced by `mindpalace_allowed_corpus` policy, `migrations/008_tenant_scoped_access.py:120-128`).
- **Path filter**: `MemoryRequest.path` filters by exact document path match in `memory.py` (line 1250: `d.path = :path`); `memory_public.py:445` raises `document_not_found` unless a version path equals request.path. Not a prefix/directory match.
- **Doc state** (`info_state`): written at `api/services/memory.py:1168-1171` but NOT used as a filter in `current`/`pack`/`query` (those filter only `v.event <> 'DELETED'` at `api/services/memory.py:476,617,1372`). **Probe c (issue #105 comment 6555683810) confirmed:** rejected content appears in pack by default (see #114 for the fix).

**Concrete test of absence of leak:** V1 comparison (issue #84): 202 tests combined across all nine tenant-isolation files (not `test_tenant_isolation.py` alone), zero outcome differences between main run and rerun. `tests/test_scope_forgery.py` proves forged scopes return zero rows. `tests/test_corpus_isolation.py` proves cross-corpus reads return zero.

One corpus per brand, one folder per campaign. A generation call scopes with
`path_prefix`, for example `mp.memory.pack(query="", budget=8000,
path_prefix=["brand", "campaigns/spring"])`, so the brand-wide documents and one
campaign's documents are read together while another campaign's briefs stay out of
the pack. Two briefs built from one template do not leak into each other's packs:
a conflict group keeps only its in-scope sides, and one side left becomes current.

### 2.3 Budget semantics (MP-SE-22)

**Verified by probe f (run 41287500689, raw output issue #105 comment 6555683810).** Derived from code reading + live probe.

- `ContextPack` budgets are in **Unicode characters**, not tokens (`memory_pack.py`, `ContextPack.budget`; `api/routers/memory.py:244` `POST /pack`). docs/STATUS.md §5: "pack construction is ~0.2 ms at every size measured".
- The sealed benchmark's `token_budget` (1200) is in characters, per the pack's `budget_unit`.
- **Probe f confirmed:** live pack response includes `"budget_unit": "unicode_characters"`. A `budget=1200` request returns at most 1200 Unicode characters of claim text, not 1200 tokens.

### 2.4 Metadata filters (MP-SE-21)

**Code reading only (no live probe needed).**

- Arbitrary key/value filter: **absent**. `MemoryRequest` supports `query`, `path` (exact document path, memory.py:1250), `as_of`, `include_history`. No `metadata` filter. See #124 for prefix scoping gap.
- What exists: exact document path match, doc state (not used as filter — see MP-SE-17), corpus scope (RLS), temporal (`as_of`), `include_history` boolean.
- Consumer adapter must encode platform/campaign in path or corpus name, or wait for a general metadata filter.

### 2.5 Isolation (MP-SE-12/28)

**Partially verified.** Probe e (run 41299138837, raw output issue #105 comment 6556969596): over HTTP against real server, restricted credential (corpus A only) calling history/as-of/evidence/snapshot/replay_snapshot/changes/pack/query/current/feed on corpus B returns 403/404 (denied). Conflicts exposed as a field in query/pack responses, not a separate route. **Unverified:** HTTP-level replay_snapshot (requires snapshot_id from prior call). Code-level tests remain evidence.

From code: every archive row carries `corpus_id`; RLS policies keyed on `corpus_id` (migration 008) or via document (`chunks` through `documents`, migration 008). Runtime role has no BYPASSRLS (`scripts/db/runtime_roles.sql`).
From tests: V1 comparison (issue #84): 202 tests combined across all nine tenant-isolation files, zero outcome differences between main run and rerun; both connect as `mpadmin`. `tests/test_scope_forgery.py`: forged scope, wrong-secret token, empty token → zero rows. `tests/test_corpus_isolation.py`: cross-corpus reads return zero.

**Specific unverified item:** HTTP-level replay_snapshot (requires snapshot_id from prior call).

### 2.6 Failure behaviour (MP-SE-26)

**Verified by probe g (run 41287500689).**

Verified from code (`mindpalace_sdk.py`):
- SDK raises `MemoryClientError` with `code`, `message`, `status_code` on HTTP errors (`mindpalace_sdk.py:139`).
- Default timeout is 30s (`mindpalace_sdk.py:918`: `timeout: float = 30`), passed as `timeout=self._client.timeout` to httpx calls (`mindpalace_sdk.py:331,447,604,819`).
- `httpx.TimeoutException` → `MemoryClientError("timeout", ..., 504)` (`mindpalace_sdk.py:334,450,607,822`).
- `httpx.RequestError` → `MemoryClientError("transport_error", ..., 503)` (`mindpalace_sdk.py:336,451,606,821`).

**Verified by probe g (run 41287500689, raw output issue #105 comment 6555683810):**
- No automatic retry found in SDK code (no retry loop/retry library import observed). **Probe g confirmed:** connection refused to port 9999 → `MemoryClientError` (transport_error, 503) raised immediately; no hang, no retry. SDK default timeout 30s confirmed from source (`mindpalace_sdk.py:918`).

## 3. Prioritised new issues (owner decides cut line)

Each line = one issue, one small PR. Cut line is "owner decides" for every item.

| # | Gap | Priority | Cut line | Notes |
|---|---|---|---|---|
| 1 | Single-claim withdraw/tombstone endpoint (MP-SE-15) | P0 | owner decides | Consumer needs per-claim withdraw. **Probe a (issue #105 comment 6555683810):** `remember` with changed content (same key) creates MODIFIED version (V2); prior claim (V1) moves to `historical_memories` as SUPERSEDED — so same-key re-remember DOES retire. **Probe d/e v2 (issue #105 comment 6556424199):** no route found to retire a claim without supplying a replacement statement (tried `POST /remember` with `supersedes_claim_id`/`topic_key`, `POST /revalidate`, `DELETE` — none exist). `sync` after file deletion adds DELETED tombstone; code filters `v.event <> 'DELETED'` (api/services/memory.py:476,617,1372). See gap #1 in §2.1. |
| 2 | Token-aware context budgets (MP-SE-22, MP-SE-07) | P1 | owner decides | Today characters; report assumes tokens. Cost: tokenizer dep. |
| 3 | General metadata filters (MP-SE-21) | P1 | owner decides | Path prefix + state exist; arbitrary key/value not. Keep core domain-neutral. |
| 4 | SDK timeouts + retry guidance (MP-SE-26) | P1 | owner decides | Default timeout 30s; no automatic retry found in SDK code. |
| 5 | Persistent claim-vector index (MP-SE-31) | P2 | owner decides | Candidate. |
| 6 | Archive pagination/scaling (MP-SE-30) | P2 | owner decides | Known cliff ~1,200 versions. |
| 7 | Marketing eval benchmark (MP-SE-27) | P1 | owner decides | Domain-neutral benchmark exists (agency_readiness); consumer-side benchmark belongs in adapter. |
| 8 | Retention/TTL/purge (MP-SE-29) | P0 (real deploy) | owner decides | Erasure exists; TTL/purge not. |
| 9 | Production monitoring/metrics (MP-SE-32) | P2 | owner decides | Readiness + posture exist; no metrics endpoint. |

Issues #107-#113 were filed for rows 2-8. Issue #105 was filed for live probes (covering §2.1-2.6 unknowns). Issue #106 was filed for the hoisting measurement (Stage 2).

## 4. Belongs-in summary

- **core**: memory primitives, RLS, scope tokens, snapshots, evidence, pack, history, as-of, revalidate, SDK, REST, CLI, MCP.
- **Mind Palace example**: `examples/marketing-campaign-memory` (adapter pattern, clearly labelled consumer).
- **consumer adapter (Team 052)**: brand/campaign entities, correction classification, authority evaluation, context assembly, outbox/retry, marketing retrieval ranking, metadata mapping, generation provenance logging.

No marketing concepts should enter Mind Palace core.

## 5. Notes

- Attachment D's status column is partly out of date (it claims auth/tenant/erasure/structured ingestion are missing; all four have shipped in some form). This gap analysis supersedes it for the items verified above.
- Several "absent" items (structured ingestion, correction classification, outbox) are correctly absent from *core* — they belong in the consumer adapter, not Mind Palace. The report's "not established" verdicts for those are accurate for core but misleading if read as "Mind Palace should build them."
- Live probes (#105, run 41287500689, raw output issue #105 comment 6555683810) completed 2026-11-01. Probe a confirmed idempotency/supersession; probe c confirmed draft/rejected/superseded appear in current/pack by default; probe f confirmed budget in Unicode characters; probe g confirmed SDK failure behaviour. Probe d (run 41296378275, raw output issue #105 comment 6556588065) confirmed exact-path scoping works; probe e (run 41299138837, raw output issue #105 comment 6556969596) confirmed cross-corpus denial over HTTP.
- Issue #105 is the live probe issue (not a throwaway PR); its results are evidence for this gap analysis.
