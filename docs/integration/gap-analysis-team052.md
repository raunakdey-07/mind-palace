# Gap analysis: Mind Palace vs. Team 052 requirements

Read-only analysis against the Team 052 requirements report (attachment D,
34 requirements MP-SE-01..34) and the Mind Palace repository at
`origin/main` (commit `023bead`, post-#100). Status column of attachment D was
written without running this repo and is partly out of date; the rows below
carry our own evidence.

**Rule:** no core changes in this PR. Each real gap becomes a separate issue
with a v0.10.0 / later recommendation; the owner decides the cut line.

## 1. Capability matrix

| ID | Requirement | Priority (report) | Verdict | Evidence | Belongs in |
|---|---|---|---|---|---|
| MP-SE-01 | Persistent memory storage | P0 | **exists** | `api/routers/memory.py:63` (`POST /remember`), `api/services/memory.py` (claims/evidence versions); tests `test_memory_core.py`, `test_memory_http.py` | core |
| MP-SE-02 | Historical memory | P0 | **exists** | `api/routers/memory.py:150` (`POST /history`), `api/routers/memory.py:226` (`POST /as-of`); `memory.py` history lineage; `tests/test_history_lineage.py` | core |
| MP-SE-03 | Evidence provenance | P0 | **exists** | `api/routers/memory.py:220` (`POST /evidence`); `memory_evidence` table, `MemoryResponse.evidence`; `tests/test_memory_multiple_evidence.py` | core |
| MP-SE-04 | Supersession handling | P0 | **exists** (limitations) | `memory.py` supersession edges; `tests/test_cross_document_supersession.py`, `test_adversarial_supersession.py`; conflict scan is keyed (docs/STATUS.md §3) | core |
| MP-SE-05 | Conflict representation | P0 | **exists** | `MemoryResponse.conflicts`; `api/services/memory.py` conflict scan; `tests/test_memory_adjudication.py` | core |
| MP-SE-06 | Semantic retrieval | P1 | **exists** (quality limits) | `api/routers/query.py`, `api/routers/search.py`, `memory_query.py`; `tests/test_search_semantics.py` (18 backend skips); frozen M006.75 45/60 exact | core |
| MP-SE-07 | Bounded context packing | P1 | **exists** (chars, not tokens) | `api/routers/memory.py:244` (`POST /pack`), `memory_pack.py`, `ContextPack`; budget in Unicode characters (see §3.3) | core |
| MP-SE-08 | Python SDK | P0 | **exists** | `mindpalace_sdk.py` (53 KB); `tests/test_sdk.py`, `test_memory_client.py`; typed errors documented (see I5) | core |
| MP-SE-09 | REST API | P0 | **exists** | `api/routers/*.py` (memory, corpora, ingest, context, query, search); `examples/MEMORY_API.md` | core |
| MP-SE-10 | Immutable snapshots | P2 | **exists** | `api/routers/memory.py:232` (`POST /snapshot`), `:238` (`POST /replay`); `memory_snapshot_seals`; `tests/test_memory_snapshot_seal.py` | core |
| MP-SE-11 | Change feed | P2 | **exists** | `api/routers/memory.py:156` (`GET /feed`); `FeedResponse`; pagination documented (no exactly-once guarantee) | core |
| MP-SE-12 | Workspace/tenant authorization | P0 | **partial** | Credentials + RLS per corpus exist (`migrations/008`, `015`, `docs/security.md`); but no workspace/organisation entity; isolation test list needs verification (see §3.5) | core |
| MP-SE-13 | Brand/campaign isolation | P0 | **partial** | Primitives exist (corpus, path, state); no brand/campaign concept in core (by design — belongs in consumer adapter). See §3.2 | consumer adapter |
| MP-SE-14 | Structured marketing ingestion | P0 | **absent** (as native) | `POST /api/memory/remember` exists (`api/routers/memory.py:63`); no marketing event schema. Belongs in consumer adapter (`examples/marketing-campaign-memory`). | consumer adapter |
| MP-SE-15 | Stable source mapping | P0 | **partial** | `source_hash`, `path`, `version_id` in `memory_documents`/`memory_versions`; idempotency on identical `remember` (docs/STATUS.md). Update semantics need verification (see §3.1) | core + adapter |
| MP-SE-16 | Idempotent event ingestion | P1 | **partial** | Idempotent `remember` returns same `version_id` with `changed=false` (docs/STATUS.md "Idempotency"). Outbox/retry not in core (consumer adapter). | consumer adapter |
| MP-SE-17 | Memory authority hierarchy | P0 | **partial** | `info_state` (approved/draft/superseded/rejected/…), `provenance_state`; conflict scan keyed. No consumer-side authority evaluation. | core (primitives) + adapter (evaluation) |
| MP-SE-18 | Correction classification | P1 | **absent** (core) | No correction type in core. Report §9.2 proposes 5 categories. Belongs in consumer adapter. | consumer adapter |
| MP-SE-19 | Persistent human-feedback workflow | P1 | **partial** | `remember` + supersession + history exist; correction promotion policy not in core. | consumer adapter |
| MP-SE-20 | Approved/rejected example distinction | P1 | **partial** | `info_state` distinguishes approved/rejected/superseded; rejected content must be excluded by consumer query (verify §3.2). | core (state) + adapter (query) |
| MP-SE-21 | Marketing-specific retrieval | P1 | **partial** | Path prefix, doc state, corpus scope exist; no arbitrary key/value filter (see §3.4). | core candidate (general metadata filter) |
| MP-SE-22 | Token-aware context builder | P1 | **absent** | Pack budgets in Unicode characters, not tokens (see §3.3). | core candidate |
| MP-SE-23 | Generation-service integration | P1 | **partial** | `POST /api/memory/pack`, `POST /api/context`, SDK; no generation orchestration in core (correct — belongs in adapter). | consumer adapter |
| MP-SE-24 | Generation provenance | P2 | **partial** | `memory_proof` / `memory_receipt` / `memory_pack` digests; `source_hash`, `version_id`, `observed_at` in responses. | core (primitives) + adapter (logging) |
| MP-SE-25 | Durable sync/retries | P1 | **absent** (core) | No outbox in core; report §7.4 recommends app-level outbox. Belongs in consumer adapter. | consumer adapter |
| MP-SE-26 | Graceful memory-service failure | P1 | **partial** | SDK raises `MemoryClientError` with code/message/status; timeouts/retries need verification (see §3.6). | core (SDK) + adapter |
| MP-SE-27 | Marketing evaluation benchmark | P1 | **absent** | No marketing benchmark in core; agency_readiness benchmark is domain-neutral marketing-shaped fixtures (not a consumer benchmark). | Mind Palace example / later |
| MP-SE-28 | Multi-user isolation tests | P0 | **exists** (core) | `tests/test_tenant_isolation.py`, `test_tenant_connection_isolation.py`, `test_tenant_control_effectiveness.py`, `test_scope_forgery.py`, `test_credential_management.py`, `test_error_and_log_hygiene.py` (V1 comparison: 202 tenant-isolation tests, zero outcome differences between main run and rerun; issue #84). | core |
| MP-SE-29 | Retention and permanent erasure | P0 (real deploy) | **partial** | `DELETE /api/corpora/{name}` → `mindpalace_erase_corpus` (migration 009); append-only triggers remain; receipts/packs/backups survive (docs/security.md §data lifecycle). No TTL/purge. | core (primitives) + adapter (policy) |
| MP-SE-30 | Archive pagination/scaling | P2 | **partial** | `GET /feed` paginated; full-corpus load documented cliff up to ~1,200 versions (docs/STATUS.md §2). | core |
| MP-SE-31 | Persistent claim-vector index | P2 | **absent** | `memory_claim_embeddings` table exists (migration 007) but `recall` recomputes vectors (docs/STATUS.md §7). | core candidate (later) |
| MP-SE-32 | Production monitoring | P2 | **partial** | `/health/ready`, `mindpalace posture`, `mp_scope_enforcement_status`, structured logs. No metrics endpoint. | core (later) |
| MP-SE-33 | General application integration guide | P2 | **partial** | `examples/MEMORY_API.md`, `examples/marketing-campaign-memory/`; `docs/integration/` does not exist yet (being built in v0.10.0 I-series). | core (docs) |
| MP-SE-34 | Secure production deployment validation | P0 (real deploy) | **partial** | Server profile refuses unsafe role, scope-secret mismatch, enforcement off (`api/main.py` lifespan, docs/security.md). Validated by CI ("A server deployment refuses/starts"). | core |

## 2. Answers to the specific questions (verified from code + existing tests)

### 2.1 Stable source identity and update semantics (MP-SE-15/16)

**Cannot run live HTTP against a server in this environment (no Postgres).** Verified from code and existing tests instead.

What the code does (from `api/routers/memory.py:63` `remember`, `api/services/memory.py`, `memory_documents`/`memory_versions` schema):
- `remember` with the same statement + key + corpus returns the same `version_id` and `changed=false` (docs/STATUS.md "Idempotency": "An identical `remember` returns the same `version_id` and `changed=false`. Four concurrent identical writes produce exactly one version and one claim.").
- A changed statement on the same key creates a new version and supersession edge (`memory_versions` keyed on `(corpus_id, memory_document_id, version_number)`; cross-document supersession migration 011).
- Withdrawal: `DELETE /api/corpora/{name}` runs `mindpalace_erase_corpus` (migration 009) which removes all archive rows for the corpus. There is no single-claim tombstone endpoint; tombstoning a single claim is not supported today (partial gap).

**Verdict:** stable source identity via `(corpus, path, key)` works; idempotency documented and tested (`test_memory_core.py`, `test_idempotency`-style). Single-claim withdraw/tombstone is **absent** — only whole-corpus erasure exists. A withdrawn instruction would need to be modelled as a new superseding claim or corpus erasure.

### 2.2 Scope separation (Test D and F of attachment D)

Mind Palace primitives:
- **Corpus boundary** (RLS per corpus, migration 008/015): strongest isolation. Campaign A and Campaign B of the same brand are different corpora → no cross-read.
- **Path filter**: `MemoryRequest.path` filters by path prefix in `memory.py` query. Tested in `test_corpus_isolation.py`, `test_corpus_scope.py`.
- **Doc state** (`info_state`: approved/draft/superseded/rejected/…): a rejected claim's state is `rejected`; `current`/`pack` exclude non-approved states by default. Tested in `test_memory_adjudication.py`.
- **Leak risk**: attachment D §6.3 notes `/api/memory/*` conflict retrieval can return conflicting evidence outside a path filter. This is by design — conflicts are corpus-scoped, not path-scoped, and the RLS boundary is the corpus, not the path. A consumer must not rely on path filtering for authorization (report §6.3 says so explicitly).

**Concrete test of absence of leak**: `tests/test_tenant_isolation.py` (V1 comparison: 202 tenant-isolation tests, zero outcome differences between main run and rerun; issue #84). `tests/test_scope_forgery.py` proves forged scopes return zero rows. `tests/test_corpus_isolation.py` proves cross-corpus reads return zero.

**Rejected copy as positive example**: `info_state='rejected'` excludes from `current`/`pack` (default filter). But there is no consumer-side query that explicitly filters "approved only" by path — the consumer must pass the right state filter. Partial gap: the SDK/REST default is correct, but the consumer must not override it.

### 2.3 Budget semantics (MP-SE-22)

- `ContextPack` budgets are in **Unicode characters**, not tokens (`memory_pack.py`, `ContextPack.budget`). docs/STATUS.md §5: "pack construction is ~0.2 ms at every size measured". The sealed benchmark's `token_budget` (1200) is also characters.
- Token-aware option: **absent**. Would cost a tokenizer dependency (tiktoken or model tokenizer) and a budget conversion. Candidate for "later" (integration kit I-candidates).

### 2.4 Metadata filters (MP-SE-21)

- Arbitrary key/value filter: **absent**. `MemoryRequest` supports `query`, `path` (prefix), `as_of`, `include_history`. No `metadata` filter.
- What exists: path prefix, doc state (`info_state`), corpus scope (RLS), temporal (`as_of`), `include_history` boolean.
- Consumer adapter must encode platform/campaign in path or corpus name, or wait for a general metadata filter (candidate "later").

### 2.5 Isolation (MP-SE-12/28)

Verified from existing tests (V1 comparison: 202 tenant-isolation tests, zero outcome differences; issue #84):
- History: corpus-scoped via RLS; a credential scoped to corpus A cannot read corpus B's history. `tests/test_tenant_isolation.py`, `test_corpus_isolation.py`.
- As-of: same RLS boundary. `tests/test_memory_load_equivalence.py`.
- Snapshot/replay: snapshot rows carry `corpus_id`; RLS applies. `tests/test_memory_snapshot_seal.py`.
- Conflicts: corpus-scoped. `tests/test_memory_adjudication.py`.
- Evidence: corpus-scoped. `tests/test_memory_multiple_evidence.py`.
- V1 proof (issue #84 comment): 202 tenant-isolation tests identical outcomes and durations between main run and rerun; both connect as `mpadmin`. No leak demonstrated.

**Cannot run live HTTP in this environment (no Postgres).** Existing test suite is the evidence; it is extensive (202 + tenant isolation + scope forgery + error hygiene).

### 2.6 Failure behaviour (MP-SE-26)

From `mindpalace_sdk.py` and `mindpalace_errors.py`:
- SDK raises `MemoryClientError` with `code`, `message`, `status` on HTTP errors (unified in v0.9.0 behaviour changes, docs/STATUS.md).
- On service down: `httpx.ConnectError` propagates (no automatic retry in SDK — retry is consumer's responsibility; idempotency stated per call in integration kit I5).
- Timeouts: httpx default (no explicit timeout in SDK today). Candidate for I5.
- Readiness: `/health/ready` returns 503 with `checks.scope_token` when enforcement is misconfigured (`docs/security.md`); `mp_scope_enforcement_status` for detailed probe.
- No retries in SDK (consumer adapter implements outbox/retry per report §7.4).

## 3. Prioritised new issues (owner decides cut line)

Each line = one issue, one small PR. Recommendation: v0.10.0 = in default scope (integration kit I1-I7 or Stage 3's own); later = deferred.

| # | Gap | Priority | Recommendation | Notes |
|---|---|---|---|---|
| 1 | Single-claim withdraw/tombstone endpoint (MP-SE-15) | P0 | v0.10.0 (later if scope creep) | Only whole-corpus erasure exists today. Consumer needs per-claim withdraw. |
| 2 | Token-aware context budgets (MP-SE-22, MP-SE-07) | P1 | v0.10.0 (I-candidate) | Today characters; report assumes tokens. Cost: tokenizer dep. |
| 3 | General metadata filters (MP-SE-21) | P1 | later | Path prefix + state exist; arbitrary key/value not. Keep core domain-neutral. |
| 4 | SDK timeouts + retry guidance (MP-SE-26) | P1 | v0.10.0 (I5) | No explicit timeout/retry in SDK today. |
| 5 | Persistent claim-vector index (MP-SE-31) | P2 | later | Candidate. |
| 6 | Archive pagination/scaling (MP-SE-30) | P2 | later | Known cliff ~1,200 versions. |
| 7 | Marketing eval benchmark (MP-SE-27) | P1 | later | Domain-neutral benchmark exists (agency_readiness); consumer-side benchmark belongs in adapter. |
| 8 | Retention/TTL/purge (MP-SE-29) | P0 (real deploy) | later | Erasure exists; TTL/purge not. |
| 9 | Production monitoring/metrics (MP-SE-32) | P2 | later | Readiness + posture exist; no metrics endpoint. |

## 4. Belongs-in summary

- **core**: memory primitives, RLS, scope tokens, snapshots, evidence, pack, history, as-of, revalidate, SDK, REST, CLI, MCP.
- **Mind Palace example**: `examples/marketing-campaign-memory` (adapter pattern, clearly labelled consumer).
- **consumer adapter (Team 052)**: brand/campaign entities, correction classification, authority evaluation, context assembly, outbox/retry, marketing retrieval ranking, metadata mapping, generation provenance logging.

No marketing concepts should enter Mind Palace core.

## 5. Notes

- Attachment D's status column is partly out of date (it claims auth/tenant/erasure/structured ingestion are missing; all four have shipped in some form). This gap analysis supersedes it for the items verified above.
- Several "absent" items (structured ingestion, correction classification, outbox) are correctly absent from *core* — they belong in the consumer adapter, not Mind Palace. The report's "not established" verdicts for those are accurate for core but misleading if read as "Mind Palace should build them."
- Verified experiments (source identity, isolation, budget, metadata filters, failure behaviour) could not be run live in this environment (no Postgres). The answers above are derived from code reading + existing test suites (202 tenant-isolation tests, V1 proof, check_versioning, check_evidence). Live HTTP verification is owed before the release.
