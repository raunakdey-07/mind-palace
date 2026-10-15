# Mind Palace — Open-Source AI Memory Infrastructure Roadmap

> **Purpose of this file:** This is the master execution prompt for the next phase of Mind Palace. Give this entire document to the coding agent at the start of the phase. The agent must work through the milestones in order, inspect the repository before changing anything, validate every claim empirically, and continue until the milestone is genuinely complete. Do not stop merely because the implementation compiles or the tests pass.

---

# 0. Mission

You are continuing development of **Mind Palace**.

Mind Palace is pivoting from being primarily a personal RAG/knowledge-management portfolio project into a focused open-source infrastructure product:

> **Mind Palace makes it easy for an AI system to remember a provided corpus.**

The product should let a developer take a corpus — notes, documentation, project files, research, manuals, internal knowledge, etc. — and make that corpus reliably available to AI systems as durable, searchable context.

This is **not** a generic chatbot, generic vector database, generic RAG demo, or generic "AI memory" wrapper.

The product must solve one specific problem exceptionally well:

> **Given a corpus, make it persistently retrievable and usable as context by AI applications with minimal integration effort.**

The name **Mind Palace** remains intentional. It is a personal-project/open-source brand inspired by the Sherlock "mind palace" concept: a place where information is deliberately organized and retrievable. Do not redesign the project around the TV show, copyrighted imagery, character likenesses, or imitation branding. The reference is conceptual, not visual.

Do not optimize for flashy UI, AI-generated aesthetics, unnecessary dashboards, or feature count. Optimize for:
- correctness
- excellent retrieval
- predictable semantics
- easy ingestion
- easy integration
- explainability
- reproducibility
- developer experience
- open-source usability

The project should eventually feel like infrastructure that another developer can actually install and trust.

---

# 1. Current State — DO NOT REDO COMPLETED WORK

Before touching code, inspect the repository and git history.

The project has already completed:

- ingestion pipeline
- deterministic document IDs
- incremental ingestion
- chunking guarantees
- embeddings
- pgvector storage
- vector retrieval
- hybrid retrieval
- RRF
- optional cross-encoder reranking
- retrieval evaluation framework
- 202-document evaluation corpus
- 671 chunks in the expanded corpus
- 98 benchmark queries
- ground-truth validation
- bootstrap confidence intervals
- paired strategy comparison
- search semantics regression tests
- grounding tests
- API backend-error semantics
- `/ask` context budgeting
- migration contract tests
- pgvector/pgcrypto/pg_trgm provisioning
- CI database migration flow
- Neo4j evidence review

The latest evidence showed:

- 202 documents / 671 chunks
- vector, hybrid, hybrid+RRF statistically indistinguishable on Recall@3 at the current benchmark size
- reranking produces small point-estimate gains but is dramatically slower
- hybrid+RRF remains the default because of robustness and score-scale properties
- vector-only remains a documented fast path
- reranking remains opt-in
- Neo4j is not justified by current workloads
- the corpus needs to become a real, useful product corpus rather than merely a benchmark corpus
- deletion propagation is still incomplete
- reranking latency remains unresolved
- grounding remains prompt-based rather than formally verified

**Do not replace working architecture simply because a different technology is fashionable.**

The next phase is about turning the proven retrieval core into a genuine AI-memory infrastructure product.

---

# 2. Product Thesis

Keep this distinction clear throughout implementation:

### Corpus memory

Mind Palace primarily remembers **provided knowledge**:

- repositories
- Markdown
- documentation
- research
- notes
- PDFs/textual knowledge after extraction
- project knowledge
- organizational documentation
- domain corpora

This is different from:

### Agent memory

Agent memory stores:
- conversations
- user preferences
- episodic experiences
- decisions
- observations
- evolving facts

Agent memory may become a future extension, but **do not let it hijack the current product**.

The core product is:

> **Corpus → durable indexed knowledge → retrieval/context interface → AI application**

---

# 3. Milestone 0 — Repository/Product Audit

Before implementation:

1. Inspect the full repository structure.
2. Read README and all architecture/evaluation documentation.
3. Inspect:
   - ingestion
   - parser
   - chunking
   - embedding
   - repository/database layer
   - retrieval
   - API routers
   - CLI
   - migrations
   - tests
   - benchmark runner
   - CI
4. Inspect recent git history.
5. Identify anything already implemented that overlaps the milestones below.
6. Do not duplicate existing abstractions.
7. Establish a clean baseline:
   - tests
   - lint
   - formatting
   - migration
   - benchmark
   - git status

Write down the baseline before modifying anything.

If the working tree contains unrelated user changes, do not destroy them. Isolate your work.

---

# 4. Milestone 1 — Product Boundary & Public API Contract

Define the product around a small, coherent public contract.

The core lifecycle should conceptually be:

```text
create corpus
    ↓
ingest / sync corpus
    ↓
inspect corpus
    ↓
search / retrieve
    ↓
pack context for an AI task
    ↓
AI application consumes context
```

Design and document stable APIs for:

### Corpus management
- create corpus
- inspect corpus
- list corpora
- delete corpus

### Ingestion
- ingest a document
- ingest a directory/repository
- sync a corpus
- inspect ingestion status
- report failures

### Retrieval
- search
- retrieve by document
- retrieve by chunk
- filters
- configurable strategy
- configurable k

### Context
Add a first-class concept of **context packing**.

Do not make developers reconstruct context manually from raw search results.

A context endpoint/service should be able to produce something like:

```json
{
  "query": "...",
  "context": "...",
  "sources": [...],
  "chunks": [...],
  "token_estimate": 1234,
  "strategy": "hybrid_rrf",
  "truncated": false
}
```

The exact schema should follow repository conventions.

The critical principle is:

> Search answers "what is relevant?"  
> Context packing answers "what should I give the model?"

These are related but distinct responsibilities.

Add contract tests for every public behavior.

---

# 5. Milestone 2 — Corpus Isolation / Multi-Tenancy Boundary

The product must support more than one corpus cleanly.

A corpus must be an explicit namespace.

Examples:

```text
mind-palace://corpora/my-project
mind-palace://corpora/company-docs
mind-palace://corpora/research
```

Do not assume one global database.

Introduce explicit corpus identity throughout:

```text
Corpus
 ├── Documents
 │    └── Chunks
 │         └── Embeddings
```

Requirements:

- searches cannot leak across corpora
- ingestion cannot accidentally overwrite another corpus
- IDs remain deterministic within a defined scope
- filters respect corpus boundaries
- deletion is scoped correctly
- benchmarks can specify a corpus
- tests prove cross-corpus isolation

This is a foundational product requirement.

---

# 6. Milestone 3 — Ingestion as a First-Class Synchronization System

The current ingestion pipeline works, but now make it product-grade.

Implement and test:

### New
A document is indexed.

### Unchanged
A document is detected as unchanged and skipped.

### Changed
The document is reprocessed and stale chunks are replaced.

### Deleted
A document removed from the source corpus is removed from the index.

This is explicitly required.

Build a proper reconciliation/sync model rather than relying on accidental behavior.

For filesystem/repository ingestion:

```text
source snapshot
       ↓
compare source state with indexed state
       ↓
added / changed / unchanged / deleted
       ↓
apply transactional changes
```

Return a machine-readable sync summary:

```text
added
changed
unchanged
deleted
failed
duration
```

Test crash/failure behavior.

A failed document must not leave a false "successfully indexed" state.

---

# 7. Milestone 4 — Provenance & Evidence Model

Every retrieved chunk must be traceable back to the source.

Strengthen provenance.

A result should be able to identify:

- corpus
- document ID
- source path/URI
- document title
- document type
- heading path
- chunk order
- ingestion version/hash
- embedding model/version
- retrieval strategy
- retrieval score
- retrieval rank

Do not expose meaningless implementation details if they are not useful to consumers.

The goal is:

> An AI application should be able to answer "where did this context come from?"

Add stable source identifiers.

Add tests proving provenance survives:

```text
source → ingestion → chunk → retrieval → context pack
```

---

# 8. Milestone 5 — Context Packing as a Core Primitive

This is one of the most important milestones.

Mind Palace should not merely return chunks.

It should produce **model-ready context**.

Implement a context packer that:

1. accepts a query/task
2. retrieves candidate evidence
3. ranks it
4. deduplicates document evidence
5. applies a token/character budget
6. preserves the strongest evidence first
7. preserves source attribution
8. returns structured and textual forms

Support explicit budgets.

Example:

```text
budget = 4k tokens
budget = 8k tokens
budget = 16k tokens
budget = 32k tokens
```

Do not silently overflow the requested budget.

Test:

- multiple chunks from same document
- oversized chunks
- tiny budgets
- empty results
- duplicate evidence
- source attribution
- deterministic packing
- stable ordering

The output should be usable by any LLM client.

---

# 9. Milestone 6 — MCP Integration

Investigate and implement a first-class **Model Context Protocol** integration.

MCP is particularly relevant because it provides a standard interface for AI applications to access tools/resources. The current 2026-07-28 specification is the current target; verify the official specification/docs before implementation and do not rely on stale MCP examples.

Official MCP references:
- https://modelcontextprotocol.io/
- https://blog.modelcontextprotocol.io/posts/2026-07-28/

The MCP layer should be a thin adapter over the stable Mind Palace core, not a second implementation.

Expose a minimal, deliberate surface.

Potential tools/resources:

```text
search
retrieve
context
list_corpora
get_document
get_source
sync
```

Do not expose 30 tools merely because MCP permits them.

The integration must be:

- deterministic
- documented
- testable
- usable by common MCP clients
- independently runnable
- clearly separated from core retrieval logic

Provide a minimal example configuration.

Do not hard-code assumptions about a particular AI vendor.

---

# 10. Milestone 7 — SDK / Developer Integration

Make the product easy to embed.

Create a minimal Python client first.

The developer experience should conceptually look like:

```python
from mindpalace import MindPalace

mp = MindPalace("my-corpus")

mp.sync("./docs")

context = mp.context(
    "How does authentication work?",
    max_tokens=4000,
)
```

Exact API is your design decision, but it must be:

- small
- discoverable
- typed
- documented
- stable
- tested

Consider REST and Python access separately.

Do not create a giant framework.

The SDK should make the value proposition obvious within minutes.

---

# 11. Milestone 8 — "Bring Your Own Corpus" Quick Start

A clean installation path is essential.

A new developer should be able to:

```text
install
↓
start database
↓
create corpus
↓
sync a directory
↓
search
↓
get model-ready context
```

Aim for a genuinely short quick start.

Provide:

- Docker Compose
- local development path
- Python package path
- environment configuration
- migration command
- one example corpus
- one retrieval example
- one context example
- one MCP example

Do not require cloud services.

Local/self-hosted must remain a first-class use case.

---

# 12. Milestone 9 — AI-Framework Neutrality

Do not make Mind Palace dependent on:

- LangChain
- LlamaIndex
- a specific model provider
- a specific agent framework
- a specific UI

Integration adapters may be provided later.

The core contract should work with:

```text
OpenAI-compatible applications
Anthropic-compatible applications
local models
custom inference servers
agent frameworks
plain Python
plain HTTP
MCP clients
```

The corpus should be the durable asset.

The AI model should be replaceable.

---

# 13. Milestone 10 — Retrieval Quality Gate

The benchmark is now a serious asset.

Turn it into a quality gate.

For every meaningful retrieval change:

- run the benchmark
- compare against baseline
- calculate confidence intervals
- calculate paired differences
- detect regressions
- report latency
- report quality
- report failure categories

Do not fail CI on meaningless noise.

Use statistically defensible thresholds.

The goal is:

> A retrieval change should not silently make Mind Palace worse.

Add a machine-readable benchmark output suitable for CI.

Eventually CI should be able to say:

```text
QUALITY GATE: PASS

Recall@3:
baseline: 0.78
candidate: 0.79
difference: +0.01
95% CI: [-0.01, +0.03]

latency:
baseline: 15ms
candidate: 16ms

decision:
PASS — no statistically significant regression
```

---

# 14. Milestone 11 — Retrieval Strategy Configuration

Formalize strategy selection.

Default:

```text
hybrid + RRF
```

Optional:

```text
vector
hybrid
hybrid + RRF
hybrid + RRF + rerank
```

Make this explicit in configuration/API.

Document why.

Do not chase benchmark wins without evidence.

If future data proves vector-only is better, change the default.

If RRF stops being useful, change it.

Architecture must follow evidence.

---

# 15. Milestone 12 — Reranker Cost Engineering

Reranking is currently the major latency bottleneck.

Do not immediately replace it with a complicated architecture.

First measure:

- candidate count
- batch size
- model load time
- warm inference time
- cold inference time
- CPU vs available accelerators
- total request latency
- reranker contribution percentage

Then investigate:

- model caching
- batching
- smaller models
- ONNX
- quantization
- optional asynchronous execution
- candidate reduction
- caching

Only adopt a change if measured quality/latency justifies it.

The default path must remain fast.

---

# 16. Milestone 13 — Embedding Model Abstraction

The system currently uses one embedding model.

Create an abstraction allowing:

```text
embedding provider
embedding model
dimension
version
```

to be explicit.

Persist embedding metadata.

Do not perform a migration simply to support multiple models.

First make the architecture model-aware.

Then benchmark candidate models against the actual corpus.

Only change the default model if evidence supports it.

---

# 17. Milestone 14 — Source Format Expansion

Expand ingestion carefully.

Prioritize useful formats:

1. Markdown
2. plain text
3. source code
4. JSON/YAML
5. HTML
6. PDF/text extraction

Each parser must produce the same canonical document representation.

Do not allow format-specific retrieval semantics to leak into the retrieval layer.

Every parser must have:

- parser tests
- malformed-input tests
- metadata tests
- deterministic output tests
- provenance tests

---

# 18. Milestone 15 — Security & Trust Boundary

Once Mind Palace becomes infrastructure, security matters.

Audit:

- path traversal
- unsafe filesystem access
- arbitrary URL ingestion
- malicious documents
- prompt injection inside corpus content
- metadata injection
- SQL injection
- oversized documents
- denial-of-service ingestion
- unbounded context requests
- credentials in metadata
- accidental cross-corpus access

Important principle:

> Corpus content is data, not instructions.

Retrieved text must never automatically become trusted system instructions.

Document this explicitly.

Add adversarial tests for prompt injection content.

---

# 19. Milestone 16 — Observability

Add lightweight observability.

Every retrieval/context operation should be measurable:

```text
request
corpus
strategy
candidate count
returned count
latency
embedding latency
retrieval latency
reranker latency
context-pack latency
token estimate
```

Do not build a giant dashboard.

Structured logs and useful metrics are sufficient initially.

The goal is diagnosis.

---

# 20. Milestone 17 — Production-Ready Error Semantics

Formalize errors.

Distinguish:

```text
invalid request
corpus not found
document not found
ingestion failure
database unavailable
embedding unavailable
retrieval failure
context budget exceeded
unsupported format
configuration error
```

Use stable machine-readable error codes.

Do not make clients parse English error messages.

Test every public API error path.

---

# 21. Milestone 18 — Documentation Rebuild Around the New Product

The README must stop reading like a personal portfolio project.

The new opening should immediately communicate:

> **Mind Palace is open-source memory infrastructure for AI applications. Give it a corpus; get reliable, model-ready context.**

The README should explain:

1. what problem it solves
2. who it is for
3. what it does
4. what it does not do
5. architecture
6. quick start
7. corpus ingestion
8. retrieval
9. context packing
10. MCP
11. Python SDK
12. evaluation
13. design decisions
14. limitations
15. roadmap

Keep the Sherlock reference subtle.

Do not make the brand a gimmick.

---

# 22. Milestone 19 — Examples

Create small, realistic examples.

At minimum:

### Example A — Documentation memory

```text
docs/
  architecture.md
  auth.md
  deployment.md
```

Ask:

```text
How does authentication work?
```

### Example B — Repository memory

Point Mind Palace at a code repository.

Ask:

```text
Where is database initialization handled?
```

### Example C — Research corpus

Provide papers/notes.

Ask:

```text
Which methods were used for X?
```

### Example D — MCP

Connect an MCP-compatible client to Mind Palace.

Show the model retrieving corpus context.

Examples must be minimal and reproducible.

---

# 23. Milestone 20 — Product Differentiation Audit

Before declaring the pivot complete, explicitly compare Mind Palace against existing categories:

- vector databases
- RAG frameworks
- agent-memory systems
- MCP memory servers
- knowledge graphs
- document search systems

Do not pretend competitors do not exist.

The differentiation should be concrete.

Potential direction:

> Mind Palace is not a database and not an agent framework. It is a focused corpus-memory layer that turns arbitrary knowledge into durable, attributable, model-ready context through simple APIs and standard AI integration.

Validate whether this claim is actually true after implementation.

If not, change the product thesis.

Do not manufacture differentiation through marketing language.

---

# 24. Milestone 21 — No Generic AI Slop Rule

Throughout the entire phase, enforce these rules:

### Do not add:
- chat UI just because it is an AI project
- animated dashboards
- "AI assistant" branding
- unnecessary agents
- autonomous loops
- generic memory abstractions
- arbitrary knowledge graphs
- fashionable databases
- 20 integrations before the core is excellent
- LLM calls where deterministic code is sufficient

### Prefer:
- explicit APIs
- deterministic behavior
- measured retrieval
- provenance
- reproducibility
- strong tests
- small interfaces
- good CLI
- good documentation
- useful MCP integration
- excellent corpus synchronization

If a feature cannot be tied to the core problem, defer it.

---

# 25. Milestone 22 — Release Discipline

At the end of each significant milestone:

1. run tests
2. run lint
3. run formatting
4. run migrations from fresh DB
5. run migration idempotency
6. run benchmark
7. inspect git diff
8. update documentation
9. update changelog
10. commit coherently
11. push
12. if significant, create a GitHub release/tag

Do not create meaningless micro-releases.

---

# 26. Final Definition of Done

This phase is **not complete** merely because tests pass.

It is complete only when all of the following are true:

### Product
- Mind Palace has a clear corpus-memory identity.
- README communicates the product immediately.
- Personal-project history does not obscure the public product.

### Core
- Corpus isolation works.
- Sync handles added/changed/unchanged/deleted files.
- Provenance is preserved.
- Search is stable.
- Context packing is first-class.
- Budgets are enforced.

### Integration
- REST API is coherent.
- Python SDK is usable.
- MCP integration works.
- AI-framework neutrality is preserved.

### Quality
- Retrieval benchmark runs reproducibly.
- Ground truth is mechanically validated.
- Statistical comparison exists.
- CI can detect meaningful regressions.

### Performance
- default retrieval is fast.
- reranking is optional.
- expensive stages are measured.

### Security
- corpus content is treated as untrusted data.
- prompt-injection risks are documented and tested.
- corpus isolation is enforced.

### Developer experience
A new developer can go from:

```text
git clone
→ install
→ start
→ ingest corpus
→ search
→ context
→ connect MCP
```

without reading the entire source tree.

### Documentation
The project explains:
- why it exists
- how it works
- how to use it
- what evidence supports its design
- where it currently falls short

---

# 27. Execution Protocol — IMPORTANT

You are the engineering agent executing this roadmap.

Follow these rules strictly.

## Rule 1 — Do not stop early

Do not stop after implementing the first obvious change.

Continue through the current milestone until:
- implementation is complete
- tests exist
- tests pass
- documentation is updated
- behavior has been empirically checked
- regressions are checked

## Rule 2 — Inspect before editing

Never assume the repository matches this roadmap.

Search the code first.

Reuse existing abstractions.

## Rule 3 — Evidence over intuition

For retrieval, performance, ranking, and architecture decisions:

**measure first.**

Do not claim improvement without a benchmark.

## Rule 4 — Preserve working behavior

Before changing retrieval/ingestion:
- run the relevant tests
- run benchmark baseline
- record results

After changing:
- rerun them
- compare

## Rule 5 — No fake completion

Do not say:
- "looks good"
- "should work"
- "probably fixed"
- "tests should pass"

Run the tests.

## Rule 6 — Investigate failures completely

When something fails:

```text
observe
→ reproduce
→ isolate
→ identify root cause
→ fix
→ add regression test
→ rerun
```

Do not paper over failures with skips unless skipping is genuinely the correct contract.

## Rule 7 — Do not introduce infrastructure without justification

Before adding:
- Neo4j
- Redis
- Kafka
- another vector DB
- another LLM
- another orchestration framework

prove that the current architecture cannot satisfy the requirement.

## Rule 8 — Keep the core model-agnostic

Do not couple the core to one LLM vendor.

## Rule 9 — Keep the product narrow

The product is about making corpora memorable to AI systems.

If a feature does not materially improve that outcome, defer it.

## Rule 10 — Commit only coherent milestones

Do not mix unrelated fixes.

Each commit should explain one meaningful unit of work.

## Rule 11 — Update documentation continuously

Do not leave documentation for the end if implementation changes public behavior.

## Rule 12 — Continue until genuinely complete

At the end, perform a final audit:

```text
git status
git diff
pytest
black --check
flake8
migration from fresh DB
migration idempotency
benchmark
API smoke tests
MCP smoke tests
SDK smoke tests
documentation audit
```

Only then report completion.

---

# 28. Recommended Milestone Order

Execute in this order unless repository evidence gives a strong reason to reorder:

```text
M0  Repository/Product Audit
 ↓
M1  Product Boundary & Public API
 ↓
M2  Corpus Isolation
 ↓
M3  Ingestion Synchronization + Deletion
 ↓
M4  Provenance & Evidence
 ↓
M5  Context Packing
 ↓
M6  MCP Integration
 ↓
M7  Python SDK
 ↓
M8  Bring-Your-Own-Corpus Quick Start
 ↓
M9  AI-Framework Neutrality
 ↓
M10 Retrieval Quality Gate in CI
 ↓
M11 Retrieval Strategy Configuration
 ↓
M12 Reranker Cost Engineering
 ↓
M13 Embedding Abstraction
 ↓
M14 Source Format Expansion
 ↓
M15 Security / Trust Boundary
 ↓
M16 Observability
 ↓
M17 Error Semantics
 ↓
M18 Documentation Rebuild
 ↓
M19 Examples
 ↓
M20 Product Differentiation Audit
 ↓
M21 Anti-Generic-AI Audit
 ↓
M22 Release / Final Validation
```

You may split a milestone into smaller commits, but do not silently skip milestones.

---

# 29. Final Product Test

At the end, perform this exact conceptual test:

> "I have a folder containing 100–10,000 documents. I want an AI application to remember this corpus. Can I install Mind Palace, point it at the corpus, and give my AI application reliable, attributable, bounded context without learning a giant framework?"

If the answer is not clearly **yes**, the project is not finished.

The ultimate goal is not to build the largest AI memory system.

It is to build the **smallest, clearest, most trustworthy layer between a corpus and an AI system that needs to remember it.**
