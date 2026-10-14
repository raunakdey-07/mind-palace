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
## How an agent writes claims

**An agent cannot call `remember` over MCP.** There are 15 tools and none is
`remember`; the only writes are `sync(corpus, path)` and `memory_snapshot`. So an
agent reaches a claim by writing a document with a `claims:` block and syncing it.
`remember` — the one-statement write — exists on the CLI
(`mindpalace remember "..." --key k`), the SDK
(`MindPalace(name).remember(statement, *, corpus, key, file)`) and REST
(`POST /remember`), but not on MCP.

That is the design, not a gap. `remember` asserts a statement is a fact; an agent
asking a model to make that assertion is the thing this system exists to prevent.
An agent that has already decided can write it down, and the exact-quote rule then
holds it to the same evidence standard a person is held to.

The rule, for an agent: `evidence` must appear **verbatim** in the text the agent
wrote. Not a paraphrase, not a re-wrap, not a smart quote. Generate the prose
first, then copy the sentence out of the file into both `claim` and `evidence`.

```console
$ mkdir -p /tmp/agent-claims && cat > /tmp/agent-claims/deploy.md <<'EOF'
---
title: Deployment architecture
claims:
  - key: architecture.datastore
    value: PostgreSQL
    claim: The primary datastore is PostgreSQL.
    evidence: The primary datastore is PostgreSQL.
---

# Deployment architecture

The primary datastore is PostgreSQL. It runs in three availability zones.
EOF

$ mindpalace ingest-repo /tmp/agent-claims --corpus agentdemo
[OK] sync: +1 ~0 =0 -0 !0, 1 chunks

$ mindpalace recall "What is the primary datastore?" --corpus agentdemo
The primary datastore is PostgreSQL.
   source: deploy.md @ f62dd742a1a0
   time:   true as of 2026-10-14 06:09 UTC
```

And the rejection, which is what an agent that writes the claim and the document
separately will get:

```console
$ mindpalace ingest-repo /tmp/agent-claims --corpus agentdemo
[OK] sync: +0 ~0 =1 -0 !1, 0 chunks
  - bad.md: claim[0] evidence must be an exact substring of an archived chunk;
    searched 1 chunk(s). Copy the sentence character for character from the
    document body -- paraphrasing, re-wrapping, smart quotes and em dashes are
    all rejected
```
