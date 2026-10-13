# Competitive landscape: agent memory systems

Researched 2026-10-13 from public web pages. **Nothing here was reproduced by us.**
Every figure carries who reported it and what kind of metric it is. Re-check before
quoting externally. Mind Palace numbers come from this repository's own evidence
artifacts and are labelled development / held-out (burned) / frozen.

**Hard rule for this file:** no sentence claims Mind Palace outperforms a named
system. We have run no cross-vendor harness, so no such comparison exists.

## 1. How to read the numbers

Three kinds of score circulate and they are not comparable. Never put two kinds in one
column.

| Kind | Question it answers | Notes |
|---|---|---|
| Retrieval recall (R@k) | Did the evidence appear in the top k results? | Always at or above end-to-end accuracy. No reader model or judge involved. |
| End-to-end QA accuracy | Retrieve, then a reader model answers, then a judge grades | Depends on reader model, judge and prompt. Different vendors use different ones. |
| Multiple-choice utilisation | Pick the best response among distractors | PersonaMem v1 chance level is 25%. |

Mind Palace's own figures are end-to-end exact-grader results on question sets we
authored. They are not comparable to any figure in section 3.

## 2. Profiles

### TencentDB Agent Memory (TencentCloud/TencentDB-Agent-Memory)

Read directly 2026-10-13: `README.md`, `INSTALL.md` and `ROADMAP.md` on the default
branch `feat/server_team`, plus the GitHub API and the repository `LICENSE`.

- Licence **MIT**, confirmed from the `LICENSE` file itself. (The GitHub API reports
  `NOASSERTION` because it could not auto-detect the licence text; the file says
  "licensed under the MIT".)
- 28,369 stars and 2,752 forks at the API read on 2026-10-13.
- Current release **v2.0.3-beta.1**, published 2026-10-13 (GitHub API releases
  endpoint). `ROADMAP.md` still says "Current release: **v2.0.1-beta.1**", so the
  document and the tag have drifted apart on the same branch.
- Positioning: a *team-level memory hub* for agents. Four asset types: Chat Memory,
  Skill, LLM-Wiki, Code-Graph. Assets have owner, version, status, visibility and
  agent bindings.
- Chat memory is layered L0 conversation, L1 atom, L2 scenario, L3 persona, distilled by
  an asynchronous pipeline. `INSTALL.md` adds that a background worker extracts
  L1 -> L2 -> L3 "as thresholds are hit".
- Retrieval is BM25 plus vector plus RRF, falling back from L2/L3 to L1/L0, capped by
  item count, character budget and timeout.
- Access: `private`, `team`, `restricted` (user/role/agent ACL), `agent`; global and
  team-level roles. `INSTALL.md` documents a strict admin/business split: only `admin`
  creates Teams and users, business users own assets inside a Team.
- Integration: a proxy. Point an agent's base URL at it; no plugin, hook or MCP server.
  `INSTALL.md` states the proxy supports **9 agent clients** and shows per-agent
  configuration for Claude Code, CodeBuddy, WorkBuddy, Codex, DeepSeek Harness,
  OpenCode, Hermes, OpenClaw and Pi, plus a generic header-preselect path.
- Deployment: `INSTALL.md` documents three modes. The recommended one-shot
  `start-all.sh` runs an **interactive** walkthrough of both LLM groups (`memory` and
  `proxy`) and "immediately probes the LLM connectivity after each group", re-prompting
  until it passes. So extraction does need a working LLM endpoint. Default ports:
  memory-core 8420, panel 8125, knowledge 8424, proxy 8096.
- Storage: **sqlite by default**, zero extra dependencies. MongoDB is documented as an
  optional **experimental, off-by-default** backend (`start-all-mongo.sh`), with an
  explicit warning that switching backends does not migrate existing data.
- Reported result: PersonaMem 48% without, 76% with, "+59% relative". The README gives
  no model, no PersonaMem version, no configuration. Not reproducible from the README.
- Self-described limits: Wiki and CodeGraph build asynchronously; asset binding is
  manual; automated memory routing "still under iteration".
- **`ROADMAP.md`, next release: "Editable memories: L1 - L3"**, with the stated reason
  "Automatically extracted memories won't stay correct forever - facts expire, decisions
  get reversed, and extraction itself can be off. The panel currently only allows
  viewing and deleting." That is the vendor's own statement that extracted memories are
  not yet correctable in place.
- `ROADMAP.md` also lists L0/L1 memory search and a Cursor adapter as next-release.
- Not documented in the README or INSTALL.md read: fact-level validity windows or
  supersession, conflict surfacing, explicit abstention, offline-verifiable receipts.

### MemPalace (MemPalace/mempalace)

Read directly 2026-10-13: the canonical repository's `README.md` on `main` plus the
GitHub API. (Earlier drafts of this file relied on forks and mirrors; that is corrected.)

- Licence **MIT**, version badge 3.10.0. 59,539 stars and 7,560 forks at the API read
  on 2026-10-13.
- Local-first, pip install. Default backend ChromaDB; pluggable (sqlite_exact,
  rust_exact, milvus, qdrant, pgvector).
- Stores conversation history verbatim and does not summarize, extract or paraphrase.
  The index is structured: people and projects become **wings**, topics become
  **rooms**, original content lives in **drawers**.
- **45 MCP tools** (README), not 19.
- README documents a temporal entity-relationship graph with validity windows
  (add, query, invalidate, timeline) backed by local SQLite. This is the closest
  documented overlap with Mind Palace's supersession model.
- LongMemEval (500 questions), R@5, all reproducible from the repository per
  `benchmarks/BENCHMARKS.md`:

  | Mode | R@5 | LLM required |
  |---|---|---|
  | Raw (semantic search, no heuristics, no LLM) | **96.6%** | None |
  | Hybrid v4, held-out 450q (tuned on 50 dev) | **98.4%** | None |
  | Hybrid v4 + LLM rerank (full 500) | >=99% | Any capable model |

  Other results in the same README: LoCoMo R@10 **60.3%** raw / **88.9%** hybrid v5;
  ConvoMem average recall 92.9%; MemBench (ACL 2025) R@5 80.3%.
- **All of the above are retrieval recall**, not end-to-end QA.
- The README states it does not headline a 100% number because "the last 0.6% was
  reached by inspecting specific wrong answers". It reports >=99% with LLM rerank.
- Independent analysis attributes the raw 96.6% to verbatim storage plus ChromaDB's
  default embedder rather than to the palace metaphor. **That attribution is the
  arXiv paper's claim, not the project's.** The README itself says onboarding may use
  `embeddinggemma-300m` (multilingual, recommended) or `all-MiniLM-L6-v2` (~30 MB), so
  the embedder behind the headline is not fixed by the project.
- `arXiv:2604.21284`, "Spatial Metaphors for LLM Memory: A Critical Analysis of the
  MemPalace Architecture" (Robin Dey, Panyanon Viradecha, submitted 2026-04-23, CC BY
  4.0), reads directly: it finds the headline is attributable to "verbatim storage
  philosophy combined with ChromaDB's default embedding model (all-MiniLM-L6-v2),
  rather than to its spatial organizational metaphor per se - the palace hierarchy
  (Wings->Rooms->Closets->Drawers) operates as standard vector database metadata
  filtering". It reports "over 47,000 GitHub stars in its first two weeks", an "extremely
  low wake-up cost (approximately 170 tokens)", and a Mem0 LongMemEval rise "from
  approximately 49% to 93.4%" in April 2026.
- The AAAK lossy-abbreviation mode is reported in our own earlier review
  ([`docs/competitive/landscape.md`](competitive/landscape.md), read 2026-09-26) as
  84.2% R@5 against 96.6% raw, and that review notes the project states the layer is
  lossy and irreversible. That figure is **not** in the canonical README read on
  2026-10-13, so it is carried here as second-hand and flagged as such.
- Not documented in the README read: explicit abstention, conflict surfacing, receipts,
  multi-tenant access control.

### Others (figures are vendor- or secondary-reported)

| System | Approach | Reported LongMemEval | Caveat |
|---|---|---|---|
| Mem0 | extraction plus retrieval, platform | 94.4% (own 2026 blog); the arXiv paper says 93.4%; earlier third-party citations say 49.0% | Same system, different dates, models and metrics. |
| Zep / Graphiti | temporal knowledge graph | 63.8% to 71.2% depending on source | Zep's own LoCoMo claim of 94.7% is disputed as not comparable. |
| Supermemory | temporal grounding, versioned chains | 85.2% to 85.4% overall; knowledge-update 88.46% on one page and 99% on another | Two pages of the same vendor disagree. |
| Hindsight (Vectorize) | retain / recall / reflect | 94.6% on its own page, 91.4% elsewhere | Vendor also publishes the most detailed MemPalace critiques. |
| Mastra OM | observational memory | 94.87% | Secondary source only. |

**Not established:** the Mem0 figure could not be resolved to one number. Its own blog
says 94.4%, the arXiv paper says 93.4%, and older third-party citations say 49.0%. All
three are in this table because none can be preferred without choosing a date, a model
and a metric, and we have not done that.

## 3. Figures and what they measure

| Figure | Reporter | Metric kind | Comparable to Mind Palace? |
|---|---|---|---|
| MemPalace 96.6% R@5, LongMemEval (500q) | project, committed results | retrieval recall | No |
| MemPalace 98.4% R@5, held-out 450q | project | retrieval recall | No |
| MemPalace >=99% R@5, LLM rerank | project | retrieval recall | No |
| MemPalace LoCoMo R@10 60.3% raw / 88.9% hybrid | project's own README | retrieval recall | No |
| Tencent PersonaMem 48% to 76% | vendor README, no setup stated | multiple choice (if v1) | No |
| PersonaMem-v2 frontier 37-48%, agentic memory 55% | PersonaMem-v2 paper | accuracy, 16x fewer tokens | No |
| Mind Palace 194/202 | us, development set | end-to-end exact grader | Not external |
| Mind Palace 131/157 | us, held-out v1, burned | end-to-end exact grader | Not external |
| Mind Palace 45/60 | us, frozen M006.75 | end-to-end exact grader | Not external |

No cross-vendor ranking can be built from this table. Conflicting figures for the same
system (Mem0, Hindsight, Supermemory) show that self-reported numbers shift with model,
date and metric.

## 4. Feature comparison (documented capabilities only)

"Not documented" means not found in the sources read, not that it is absent.

| Capability | Mind Palace | Tencent | MemPalace |
|---|---|---|---|
| Verbatim source retained | yes (L0, immutable) | yes (L0) | yes |
| Fact-level validity and supersession | yes (authored claims) | not documented | yes (SQLite graph, per README) |
| Conflict surfacing | yes, **only same-key** | not documented | not documented |
| Explicit abstention | yes (`NO_RELEVANT_MEMORY`) | not documented | not documented |
| Offline-verifiable receipts | yes | not documented | not documented |
| Hybrid retrieval | lexical then semantic, optional cross-encoder | BM25 + vector + RRF | semantic default, keyword and temporal boosting in hybrid modes |
| Automatic ingestion from conversations | **no**: needs authored claims; unstructured text yields no claims | yes, LLM extraction | yes, verbatim, no LLM |
| LLM needed at ingestion | no | yes | no (core) |
| Per-asset or per-user access control | corpus-level grants plus RLS; **same-corpus privacy unsupported** | private / team / restricted / agent ACL; admin/business split | not documented |
| In-place correction of a wrong memory | yes (claims are editable, supersession is a first-class operation) | **no** - viewing and deleting only, editable in a future release per `ROADMAP.md` | not documented |
| Skills, code graph, wiki | no | yes | no |
| Integration surface | CLI, SDK, REST, MCP | proxy (base-URL swap), 9 agent clients | MCP (45 tools), CLI, agent skills |
| Install weight | 1.5 GB (CPU torch) to 5.9 GB (default) plus PostgreSQL with pgvector | Docker, three services, LLM keys | pip, ChromaDB default, ~80 MB model (default minilm) |
| Public benchmark number | none | PersonaMem (setup unstated) | LongMemEval R@5 |

## 5. What this means for Mind Palace

Established by sources read directly:

1. Higher headline numbers elsewhere are retrieval recall or a setup-unstated
   multiple-choice score. They say little about being right on current-state questions.
2. A small embedder is enough for turn-level retrieval recall: the arXiv paper
   attributes MemPalace's raw 96.6% to a MiniLM-class embedder over verbatim text. The
   same paper reports end-to-end accuracy much lower, and the project's own README
   refuses to compare itself to end-to-end numbers.
3. Knowledge update and temporal reasoning are the slices where published systems differ
   most and where one vendor reported a regression. They are also where Mind Palace's
   design (validity, supersession) is aimed.
4. **The vendor roadmap agrees with our read of the gap.** TencentDB-Agent-Memory's own
   `ROADMAP.md` puts "editable memories" in the next release because "facts expire,
   decisions get reversed, and extraction itself can be off". That is the current-state
   problem stated by the system that claims to solve it, from its own maintainers.

Established by our own repository:

4. We have no public-benchmark number and no independent question set.
5. Ingestion is our largest gap. Competitors take raw conversations; we need authored
   claims, and our own dogfood produced 11 documents and 0 claims before the warning fix.
6. Distribution and integration are far behind (proxy and plugin ecosystems elsewhere).

Claims we must not make: that Mind Palace outperforms any named system; that any figure
above is comparable to ours; that "palace" or "hub" structure improves retrieval.

## 6. Candidate wedge (a judgment, not a finding)

Be excellent at one narrow job: **what is true now, what changed, why, and proof of
it, for project decisions and facts kept in git-tracked documents.**

Why this and not conversation memory: our structural advantages (supersession,
conflict surfacing, abstention, receipts, exact-quote evidence) matter most where
facts change and answers must be auditable, and structured documents are the input we
can already ingest. Conversation memory needs an extractor we do not have.

What would make the wedge real, none of it done yet:

- an independent question set whose gold comes from git history or explicit ADR
  "superseded by" links, not from our authors;
- end-to-end and retrieval-only numbers reported separately, per slice;
- an extraction design that keeps the exact-quote evidence rule, if conversation input
  is ever supported.

## 7. Sources and verification status

All fetched 2026-10-13. Status is the HTTP result of this session's fetch.

| Source | Status 2026-10-13 | Used for | Bias or limit |
|---|---|---|---|
| github.com/TencentCloud/TencentDB-Agent-Memory `README.md`, `feat/server_team` | 200, read | Tencent profile | Vendor README. Read directly. |
| `.../INSTALL.md`, `feat/server_team` | 200, read in full | install modes, ports, LLM groups, agent clients, storage | Vendor doc. Read directly. |
| `.../ROADMAP.md`, `feat/server_team` | 200, read in full | next-release items, editable memories | Vendor doc. Read directly. Says v2.0.1-beta.1 while the release is v2.0.3-beta.1. |
| `.../LICENSE`, `feat/server_team` | 200, read | MIT confirmation | Primary. GitHub API says NOASSERTION. |
| GitHub API `repos/TencentCloud/TencentDB-Agent-Memory` | 200 | stars, forks, default branch, releases | Stars are a moving figure. |
| github.com/MemPalace/mempalace `README.md` (canonical, `main`) | 200, read in full | MemPalace profile, benchmarks, 45 MCP tools, backend table | Vendor README, read directly. |
| GitHub API `repos/MemPalace/mempalace` | 200 | stars, forks, MIT, version | Stars are a moving figure. |
| arxiv.org/abs/2604.21284 | 200, read | MemPalace critique, MiniLM attribution, 170-token wake-up, Mem0 93.4% | Independent authors; read at abstract page, not the PDF tables. |
| arxiv.org/abs/2512.06688 (PersonaMem v2) | 200, abstract only | PersonaMem context | Paper abstract and a third-party summary. |
| vectorize.io articles and benchmarks page | not fetched this session | Hindsight figures | Publisher sells Hindsight, a competitor. **Unverified.** |
| mem0.ai blog posts | not fetched this session | Mem0 94.4% | Vendor-reported. **Unverified this session.** |
| supermemory.ai research pages | not fetched this session | Supermemory figures | Vendor-reported; two pages disagree. **Unverified.** |
| atlan.com, studiomeyer, hackernoon, korben | not fetched this session | secondary figures | Secondary, some marketing. **Unverified.** |

Open verification items:

- Which PersonaMem version, model and protocol Tencent used.
- LongMemEval category counts for abstention (knowledge-update 78 and temporal 133 come
  from a secondary post that was not fetched).
- The Mem0 figure. Three numbers circulate (94.4%, 93.4%, 49.0%) and we have not chosen.
- The Zep, Supermemory and Hindsight ranges, which are unverified this session.
- The end-to-end QA figure of about 67.2% attributed to the arXiv paper. It does not
  appear in the abstract; it needs a page-level citation or should be dropped.

Update this file whenever a section 3 figure is re-checked; record the date.
