# Gate package — TRUST, VALUE, LEAN

> ## READ THIS BEFORE ANY NUMBER BELOW
>
> **Arm A in this lab is not the product path.** It ranks with the shipped
> relevance scorer but grades at **rank 1** and has **no lexical fallback**. The
> product's own benchmark, using its own grader and its documented
> lexical-then-semantic fallback, reports **194/202**. This lab's arm A reports
> **151/202**.
>
> So every figure below -- the harm matrix, the **36.4% ceiling**, every
> confident-wrong rate, every recall@k -- was measured **against arm A, not
> against Mind Palace**. They are properties of that arm under that grader.
>
> **They are NOT ESTABLISHED for the product.**
>
> The data is kept rather than deleted, because the differences are informative
> and because removing measurements is how a project loses the ability to notice
> it was wrong. What is wrong is the framing, and this banner is the correction.
>
> `docs/performance/retrieval-lab-protocol.md` is the frozen protocol, and
> `docs/STATUS.md` carries the numbers that *are* the product's.


Decision gate for the adoption round. Every number below was produced by a
command in this session; the command is named. Nothing here is applied.

**Two of the three tests cannot be answered yet.** The downstream evaluation
(the VALUE test) needs language models and there are none reachable: the only
credential has a hard `$0.00` ceiling and no local inference server is running.
The cold-reader stalls are blocked by the same wall. That is an environment
fact, not a modelling decision, and it is why those sections are marked **NOT
ESTABLISHED** rather than filled with a plausible story.

---

## a. Wrong-answer analysis

### The 4 dogfood failures

| # | question | class | mechanism |
|---|---|---|---|
| Q3 | "if two users must not see each other's memory, what should I do?" | **mis-selected** | answered from `withdrawn.md` with a SQLSTATE line. The correct span ("The supported answer is one corpus per confidentiality boundary") was in `when-to-use.md` and was not returned. |
| Q4 | "does deleting a corpus remove the data from backups?" | **mis-selected, confident** | see below |
| Q7 | "what SQLSTATE caused the remember 503?" | **mis-selected** | returned the sentence saying the endpoint returned 503 — the symptom, not the cause. The 42501 answer went to Q3 instead. Both questions pulled from the same region of the same document. |
| Q8 | "is cryptographic erasure implemented?" | **mis-selected** | returned a sentence describing what it *would* do. The document's own heading says it is not implemented. |

**Q4 specifically, because it is the one that matters.** The answer was **not
absent from the corpus**. `deletion-and-erasure.md` line 64 reads:

    | PostgreSQL backups, snapshots, replicas | **operator-controlled** | **no** — outside Mind Palace |

and line 73: *"it does not remove the backup one."* Both are in the corpus. What
happened is that the claim nearest the question was about **cryptographic
erasure**, which also mentions backups, and it was returned instead. The correct
answer was **body text, not an authored claim** — and recall ranks claims.

So: **the corpus knew, recall ranked the neighbouring claim higher, and nothing
told the reader the distinction existed.**

### The 22 wrong answers, by mechanism

Held-out v1, all 26 failures:

| mechanism | n | what it is |
|---|---:|---|
| abstained when answerable | 12 | 11 of 12 are F7 lexical — the matcher requires every query token in one claim. Concentrated in temporal (5) and provenance (4). |
| returned a related span, not the right one | 8 | F7. The expected key appeared in **zero** of the 8 — every one returned a different document entirely. |
| returned everything in the corpus | 5 | F2 authority missing. All 5 are `multi-topic` questions asking "which service owns X"; the system returned 13 keys at once. |
| returned a related key, not the owner | 1 | F9 relationship representation. |

Frozen M006.75, 13 non-exact of 60:

| mechanism | n |
|---|---:|
| answered, expected key missing only | 3 |
| answered, unexpected key present only | 4 |
| answered, both | 1 |
| abstained when answerable | 5 |

**0 invented authority** in both sets (`false_current_authority_claims: 0`,
`answered_when_it_should_have_abstained: 0` on the frozen benchmark, 1 on
held-out v1). The failure mode is *over-answering*, not fabrication.

### Options to reduce confident-wrong

None of these is applied. Each is stated with what it would cost.

| option | effect | abstention cost | schema impact |
|---|---|---|---|
| **A. return the answer's basis, not just the claim** | directly addresses Q4: the reader would see *which* line answered, and could see the adjacent one did not | none | new response field |
| **B. a match-strength or match-basis field on every claim** | the caller can rank by "exact span" over "same document, neighbouring claim" | none | new column on `memory_claims`; **schema version bump** |
| **C. surface body-text answers alongside claims** | Q4's answer was in the body. Ranking body text is what makes lexical recall worse, not better | would raise abstentions substantially | no schema change, large retrieval change |
| **D. do nothing; document that recall ranks authored claims** | honest, zero cost | none | none |

**Recommendation: A.** It is a response field, not a schema change, and it makes
the Q4 failure visible to a reader rather than invisible to them. **B** is the
more principled fix and I would take it if the schema-version cost is acceptable;
it is a `memory_claims` column and every consumer of the pack digest would need
to agree on whether it participates in the digest. That is a decision, not a
patch.

---

## b. Lean: install cost, measured this session

| path | time | venv |
|---|---:|---:|
| `pip install -e .` (README step 1) | 5 s | 13 MB |
| `pip install mindpalace-os` (wheel only) | 0 s | 13 MB — **no alembic** |
| `requirements.txt`, default index | 217 s | **5.9 GB** |
| `requirements.txt` + CPU torch index | 130 s | **1.5 GB** |

The CPU-only wheel is 40% faster and 4.4 GB smaller, and it is **mentioned
nowhere**. One README line fixes the largest thing in the first hour.

**Not yet measured, and I will not estimate it:**

* **An ONNX embedder on the same weights.** Plausible — `optimum` can export
  MiniLM — but the numbers that matter (embedding agreement with the torch
  embedder, rank agreement on dev) are *not established*. Guessing them would be
  inventing the argument for the option.
* **A `[semantic]` extra with a lexical default.** This inverts the current
  default: today a newcomer downloads 5.9 GB to get semantic ranking, and
  `MIND_PALACE_LEXICAL=1` is opt-in. Making lexical the default and semantic an
  extra is a **dev-accuracy change I have not measured** and cannot measure
  without running the dev set, which this round excludes.

---

## c. Required services

A new developer must install and run:

| | version | notes |
|---|---|---|
| PostgreSQL | 15 measured; 16 untested | `docker compose up -d postgresql` in the README |
| pgvector | 0.8.7 | **provisioned by `alembic upgrade head`** — verified, the migration creates the extension |

So `init` handles pgvector and `docker compose` handles PostgreSQL. The one
gap: **`pip install mindpalace-os` alone cannot run the migration**, because
alembic is not a declared dependency of the wheel.

**Embedded single-user mode — an option, costed as an estimate:**

Roughly 3-5 days. It would break: row-level security (the whole tenant boundary
is PostgreSQL policies), advisory locks (the append-only writers serialise on
them), and pgvector (no ANN index, so ranking would be a linear scan). It is
optionally a *different product* with a different security story, and the
security story is the part this project has spent the most effort on. I do not
recommend it, and I have not built it.

---

## d. Surface audit

Nothing is deleted. Core = needed in the first hour.

**CLI (18 commands).** Core: `init`, `remember`, `recall`, `explain`, `history`,
`receipt`, `verify`, `corpora`. Advanced: `ask`, `summarize`, `interview`,
`related`, `timeline`, `search`, `ingest`, `ingest-repo`, `reindex`, `doctor`,
`serve`.

**MCP (15 tools).** Core: `memory_recall`, `memory_explain`, `list_corpora`.
Advanced: the other twelve — `memory_as_of`, `memory_changes`, `memory_current`,
`memory_evidence`, `memory_history`, `memory_pack`, `memory_query`,
`memory_replay`, `memory_snapshot`, `context`, `search`, `sync`.

**Env vars (12).** Core: `DATABASE_URL`, `MIND_PALACE_CURSOR_SECRET`.
Conditional: `MIND_PALACE_LEXICAL`, `MIND_PALACE_RUNTIME`, `MIND_PALACE_VERBOSE`.
Advanced: the other seven.

**Proposal for the first hour:** show 8 CLI commands and 3 MCP tools in the
quickstart; leave all of them in `--help`. The MCP argument shape is the sharpest
single problem — it took the audit three attempts to get right, and it is
discoverable only through `tools/list`.

---

## e. Authoring cost

**Not established.** To measure "a developer gets from raw notes to claims" for a
20-page document I would need to author claims against one and count the passes.
I did not do it, and a number here would be invented.

What the dogfood does establish: authoring is `claims:` in the frontmatter, the
**claim text must appear verbatim** in the document, and `value` is required. The
verbatim requirement is a feature — it is why the system refuses to infer — and it
is also the reason the first dogfood attempt failed, because a hand-written
YAML block with an unquoted colon in it was silently dropped. That is now fixed
and reported.

Unstructured text has **no authoring path at all**. It is archived as body text,
retrievable through the lexical rung, and contributes **zero claims**.

---

## f. Decisions needed

1. **Response field A (answer basis)?** Cheap, no schema change, makes Q4 visible.
2. **Column B (match basis)?** Better fix; costs a schema version bump.
3. **CPU-only torch in the README?** 4.4 GB saved for one line. My recommendation:
   yes.
4. **alembic as a wheel dependency?** Or keep `pip install mindpalace-os`
   proof-only and say so more clearly.
5. **v4 question set?** Blocked on model access. Without it the next evaluation
   scores v3, which is not independent.
