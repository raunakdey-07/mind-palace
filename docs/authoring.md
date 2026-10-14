# Authoring memory

What you write determines what Mind Palace can answer. This document says
which shapes work, what each one stores today, and what the rejection messages
mean.

**Everything here was measured on 2026-10-14** by
`scripts/wedge/ingestion_inventory.py`, on a disposable corpus and a disposable
PostgreSQL. "Claims" is what `memory_claims` gained. "Passages" is the number of
`memory_evidence` rows. "Recall" is the product's own `query` operation against
that corpus.

---

## The short version

| input shape | claims | passages | recall |
|---|---:|---:|---|
| Markdown with a `claims:` frontmatter block | **2** | **2** | **answered, and correct** |
| Plain Markdown, no frontmatter | **0** | **0** | **abstains** |
| A conversation transcript, five sessions | **0** | **0** | **abstains** |

**Unstructured text currently yields no claims, and recall abstains on it.**
Not "answers less well" — abstains. The transcript in the inventory contains the
sentence *"Checkout retries a failed payment twice before giving up."* verbatim.
It was asked *"How many times does checkout retry a failed payment?"* The
document was chunked into six chunks and stored, and the product returned
nothing, because it ranks **claims**, and the document had none.

There is no extractor. A document becomes memory because a person wrote the
memory into it.

---

## Shape 1: claims frontmatter — the supported one

A YAML `claims:` block in the frontmatter, plus a body. Each claim needs four
things:

* `key` — a stable identifier, unique within the version.
* `value` — the short form, what you want quoted back.
* `claim` — a sentence, and **the document's own words**.
* `evidence` — a quote that is an **exact substring** of the body.

### A worked example that passes

```markdown
---
title: "Checkout rewrite"
document_type: decision
status: Accepted
claims:
  - key: checkout.primary_datastore
    value: "PostgreSQL"
    claim: "The checkout service uses PostgreSQL."
    evidence: "The checkout service uses PostgreSQL."
  - key: checkout.retry_policy
    value: "two attempts"
    claim: "Checkout retries a failed payment twice before giving up."
    evidence: "Checkout retries a failed payment twice before giving up."
---

# Checkout rewrite

The checkout service uses PostgreSQL. We moved it in March.

Checkout retries a failed payment twice before giving up. The third failure
surfaces to the customer as a declined card rather than a server error.
```

This ingests to **2 claims, 2 passages**, and answers
*"Which datastore does the checkout service use?"* with
*"The checkout service uses PostgreSQL."*

Note what it does **not** do: the claim sentences do not appear in the
frontmatter only, they appear in the body. That is the whole rule.

## The rule, stated once

`api/services/memory.py` enforces two things before anything is written:

1. **The `evidence` quote must be an exact substring of an archived chunk.**
2. **The `claim` text must appear in the chunk that quote matched.**

The second is the one people do not expect. A claim cannot be a paraphrase, a
summary, or an inference. It has to be a sentence the document already contains.
That is deliberate — it is what makes the claim auditable against its source,
and it is checked again by a database trigger at insert time, so it cannot be
bypassed by writing SQL.

---

## Shape 2: plain Markdown — stored, unanswerable

A document with no `claims:` block is chunked and stored. `current` returns
nothing. `query` abstains.

This is the gap the competitive review names: competitors take raw documents and
conversations, and we need an authored claim. Until there is an extractor, the
cost of that gap is **authoring**, and it is paid once per fact.

## Shape 3: a conversation transcript — the same

A five-session support log ingested as Markdown produced **0 claims, 0
passages**, and abstained, even though the answer was in the text verbatim.

Treating a transcript as memory means authoring the claims from it: the fact,
its evidence quote, and the date it stopped being true.

---

## The two rejections you will hit

Both messages come from `api/services/memory.py`, are wrapped in
`ClaimValidationError` with the document path and a version fingerprint, and
**never contain your document's text** — a rejection has to be identifiable in a
log without putting customer prose in one.

### 1. The quote is not in the document

```
document=docs/claims-frontmatter.md version=8daae9ad…:
claim[0] evidence must be an exact substring of an archived chunk;
searched 1 chunk(s). Copy the sentence character for character from the
document body -- paraphrasing, re-wrapping, smart quotes and em dashes are
all rejected
```

**What went wrong.** The quote is not a substring. Almost always one of:
re-wrapping a sentence across a line break, replacing `--` with an em dash,
curly quotes instead of straight ones, or copying from a source that formats
differently from the file.

**The fix.** Open the file, select the exact span with the cursor, paste it into
`evidence`. Do not retype it and do not retype it from memory.

### 2. The claim is not the document's words

```
document=docs/claims-frontmatter.md version=335a0cd9…:
claim[0] text must appear in the supporting chunk that its evidence matched;
semantic inference is not supported. The claim must reuse the document's own
wording
```

**What went wrong.** The quote matched, but the `claim` sentence is not in that
same chunk. Usually you wrote a clearer sentence than the document has.

**The fix.** Make `claim` a sentence from the body — the same one `evidence`
quotes, or another from the same chunk. Put the paraphrase in `value` if you
want a short form; put your reasoning in the document body, where it can be
quoted.

### How often

Measured while building this document's example: **two of three drafted
documents were rejected**, one for each rule. That is not a defect in the
checker. It is the cost of the guarantee, and it is the thing the guarantee buys.

---

## What a rejection does not do

A rejected document writes **nothing**. Validation runs before the write, and the
error names a version fingerprint so you can tell which draft failed. Nothing is
partially stored, and a later successful ingest is not affected.

## Checklist

- [ ] `key` is unique within the version and stable over time.
- [ ] `value` is the short form you want quoted back.
- [ ] `claim` is a sentence copied from the document body, not a paraphrase.
- [ ] `evidence` was pasted with the cursor from the body, not retyped.
- [ ] `evidence` does not cross a line break, and uses the document's own
      punctuation.
- [ ] A change of truth is a **new version with a new claim**, not an edit of the
      old one — supersession is what makes "what changed" answerable.

## What this document does not tell you

How to write claims *from* unstructured text. No extractor exists, so there is
nothing to document yet; `docs/wedge-decision-note.md` argues that building one
is a later decision, not this one.
### `memory_remember`

An agent can now call `memory_remember` over MCP. It is the sixteenth tool and the
first one that writes memory:

```
memory_remember(statement: str, corpus: str, key: str | None = None,
                file: str | None = None)
```

The arguments and the code path are the SDK's `remember()`, so an agent and a
script cannot write memory differently. Idempotent: the identical call again
returns the same `version_id` and `event: UNCHANGED`. A changed value on the same
`key` supersedes the old one.

It takes no credential. The caller's identity is whatever
`MIND_PALACE_MCP_CREDENTIAL` resolves to, and that identity needs write access to
the corpus — a read-only credential is refused.

**Without `file`, the evidence for the claim is the statement itself.** That is a
real limitation and worth being plain about: the evidence proves *what you were
told*, not that it is true. A model that writes `"The production root password is
hunter2"` into memory has recorded a perfectly verified claim that is entirely
false. Provenance is what this system offers, not truth.

With `file=`, the statement must appear **verbatim** in that file, and the file
must be inside `MIND_PALACE_INGEST_ROOTS` — the same bound that governs
`ingest-repo`. That is the stronger form: the claim is then anchored to a document
someone else wrote.

**Untrusted text should not become claims without review.** `statement` is
attacker-controllable wherever a model has ingested a document and been asked to
remember something from it. Nothing in this tool prevents a model from writing
something a user merely read. If the content is not yours — a fetched page, a
support ticket, a scraped repo — then `remember` is the wrong call, and
`MIND_PALACE_INGEST_ROOTS` plus human or policy review is the right one. An agent
given this tool should be given the review step too, not just the tool.

## How an agent writes claims

An agent has two ways to write memory. The older is to write a document with a
`claims:` block and call `sync(corpus, path)`. The newer is `memory_remember`,
described below. The one-statement write also exists on the CLI
(`mindpalace remember "..." --key k`), the SDK
(`MindPalace(name).remember(statement, *, corpus, key, file)`) and REST
(`POST /remember`).

`remember` asserts a statement is a fact, and handing that assertion to a model is
the thing this system exists to prevent — which is why it was the last write path
to reach MCP rather than the first.

The rule, for an agent: `evidence` must appear **verbatim** in the text the agent
wrote. Not a paraphrase, not a re-wrap, not a smart quote. Generate the prose
first, then copy the sentence out of the file into both `claim` and `evidence`.

```console
$ mkdir -p ./docs && printf '%s\n' '---' 'title: Deployment architecture' 'claims:' '  - key: architecture.datastore' '    value: PostgreSQL' '    claim: The primary datastore is PostgreSQL.' '    evidence: The primary datastore is PostgreSQL.' '---' '' '# Deployment architecture' '' 'The primary datastore is PostgreSQL. It runs in three availability zones.' > ./docs/deployment.md

$ mindpalace ingest-repo ./docs --corpus agentdemo
[OK] sync: +1 ~0 =0 -0 !0, 1 chunks

$ mindpalace recall "What is the primary datastore?" --corpus agentdemo
The primary datastore is PostgreSQL.
   source: deployment.md @ 178141e34f2d
   time:   true as of 2026-10-14 06:54 UTC
```

`evidence` is a copy of the sentence in the body of that file. Nothing is
inferred: if the two ever differ, the ingest is rejected and the document is
stored but answers nothing. An agent that generates a claim and its document
separately will produce an almost-substring and hit exactly that.

The abstention, which is what a document with no `claims:` block gives:

```console
$ mindpalace recall "What is our incident response SLA?" --corpus agentdemo
No memory matched that question.
  NO_RELEVANT_MEMORY

Only authored claims are answers. A document with no `claims:` block
in its frontmatter is stored and searchable, but contributes nothing
here. See docs/authoring.md to author claims in it.
```
