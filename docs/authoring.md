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

How to write claims *from* unstructured text. `docs/wedge-decision-note.md` argues
that a structural extractor was a later decision; one now exists and is described
under "Plain Markdown" below.

## Plain Markdown needs no `claims:` block

A document with no authored block is read for structure, with no model and no
inference. A heading path scopes what follows it, and the claims under it are:

- list items: `- `, `* `, `+ `
- numbered entries: `1.`
- `Key: Value` lines: `Status: Accepted`, `Superseded-by: ADR-003`

Each becomes one claim whose evidence is the line, quoted back from the archived
chunk, with real character offsets. Nothing is summarised, reworded or merged.

```markdown
# ADR 001: primary datastore

- The primary datastore is PostgreSQL 15.
- Writes go through the repository layer.

## Status

Status: Accepted
```

**Not extracted:** fenced and indented code, tables, HTML comments, link reference
definitions, horizontal rules, block quotes, and a document's own `# title`. A
code block holds example commands, which are not decisions.

**Identity is positional.** A claim's key is `md:<heading path>:<ordinal>`, the
ordinal counting the extracted claims under that heading path. Editing a bullet
supersedes it in place, because the key does not move when the text does. A
key derived from the text would change on every edit and leave the old claim
un-superseded, joined by a second claim under a second key. Inserting a bullet
renumbers the ones after it, so those supersede each other; that is honest, since
the document did change under them.

**An authored `claims:` block wins outright.** The reader does not run on a
document that has one. An author's key is a decision, and the tool does not
produce a second opinion beside it.

**A line that cannot be quoted is dropped and named.** When a chunk boundary falls
inside a line, the claim is not stored with evidence nobody can verify; the ingest
reports `claim_extraction_no_quote` with the line number and no content.

**A document whose frontmatter did not parse is read as no claims at all**, and
reports `frontmatter_unparseable`. That is deliberate. When frontmatter fails to
parse there is no way to tell where the block was supposed to end, so the parser
hands back the whole file, block included, as the body. Reading structure out of
that would build claims from the very `claims:` block that was just discarded.

## Quote your evidence: YAML will try to read it

A `claims:` block is YAML. Four characters will silently discard the **entire**
block — not just the one field — and the document is then stored as text that
answers nothing.

The four: a **backtick**, a **colon followed by a space**, a **leading `@`**,
and a **leading `-`/`?`/`:`**. All are ordinary in Markdown prose and all are
reserved at the start of a YAML plain scalar.

```console
$ mkdir -p ./quotecheck && printf '%s\n' '---' 'title: Bad quoting' 'claims:' '  - key: a.b' '    value: v' '    claim: c' '    evidence: `get-shit-done-cc` is the sole npm package.' '---' '' '# Bad quoting' '' 'The sole npm package is get-shit-done-cc.' > ./quotecheck/bad.md

$ mindpalace ingest-repo ./quotecheck --corpus quoting
[FAIL] sync: +1 ~0 =0 -0 !0, 2 chunks
  - bad.md: frontmatter_unparseable
```

The fix is to wrap the value in double quotes and escape any double quotes it
contains. Quoted scalars have none of these restrictions:

```yaml
    evidence: "The sole npm package is `get-shit-done-cc`."
```

Those `$ ` lines run on every push. Change the quoting and the job fails.

Since 2026-10-15 the ingest names the file and says `frontmatter_unparseable`
rather than `no_claims_declared`, which told the author the opposite of the
truth — that they had written no claims, rather than that their claims had been
discarded. The message carries a reason code and a line number and never the
document's content.

## How supersession works

A key identifies **one fact**. There are three ways a fact changes.

**1. A new version of the same document.** Re-ingest the same path with different
text. The archive keeps both, and `history` shows the chain:

```console
$ mindpalace history gsd.package_boundary
2026-10-15 03:53 UTC  recorded  Every Shared Module has exactly one hand-authored source of truth.
2026-10-15 03:56 UTC  Every Shared Module has exactly one hand-authored source of truth.
            superseded by  The sole npm package is get-shit-done-cc.

current now: The sole npm package is get-shit-done-cc.
```

**2. `mindpalace remember` with the same `--key`.** Same chain, same
`superseded by` line, produced without a document.

**3. `--supersedes <claim-id>` or `--topic-key <key>`, across files.** These retire
a claim in a *different* document, which is the case a new ADR needs:

```console
$ mindpalace remember "The primary datastore is PostgreSQL 16." \
    --corpus project --supersedes 9f2c1a4b7e30
Remembered.
  version: fbf3ef524536 (new)
  corpus:  project
  path:    memory/text-77760dda7e82.md

1 live claim(s) already read like this one:
  0.75  The primary datastore is PostgreSQL 15.
      claim 10864d5681f2 on key md:adr-001-primary-datastore-3a0fe8be:0
```

The trailing block is the near-miss hint, described under `memory_remember` below.
The retired claim is the one it names.

`recall` prints the claim id on every answer, so it is also where you find the id to
retire. `--topic-key` retires whatever is live under a key without naming one; it
must be a key the new write authors, and it is refused when two documents hold a
live claim under it, because retiring one of two would leave a disagreement and
look resolved.

Either argument takes a **prefix** of a claim id, which is the 12 characters
`recall` prints. A prefix matching more than one claim is refused rather than
resolved to the first.

`--supersedes` also takes a **phrase from the claim's own text**, for when you have
the sentence in front of you and the id on another screen:

```console
$ mindpalace remember "The primary datastore is PostgreSQL 16." \
    --corpus project --supersedes "primary datastore is PostgreSQL"
```

A 64-character lowercase-hex string is used as an id. A shorter hex string is a
prefix. Anything else is a phrase, matched on content words at the same 0.60
threshold the near-miss hint uses. A phrase matching two live claims is refused
and the error names their ids.

Both arguments are refused rather than guessed at when they do not resolve to exactly
one target. A supersession that lands on the wrong claim moves a live fact into
history, which is worse than one that does not land.

### Letting the document say it itself

A numbered decision record already knows what it replaced. Say so, and
`ingest-repo` links the two documents without anybody looking up an id:

```markdown
---
title: "ADR 0002: Use PostgreSQL 16 for the primary datastore"
supersedes: 0001-use-postgres15.md
---

# ADR 0002: Use PostgreSQL 16 for the primary datastore

## Decision

- The primary datastore is PostgreSQL 16 with three replicas.
```

`supersedes:` in frontmatter, or a `Supersedes:` line in the body, and a
`superseded-by:` line for the forward direction. The reference is read **exactly as
written**: it is a document path, or a claim key, and nothing is inferred from it.
`Supersedes: 0002` does not mean "whatever file has 0002 in the name" — resolving
that would retire whichever decision happened to sort first.

The path may be abbreviated to the part that identifies it, so
`0001-use-postgres15.md` and `examples/adr/0001-use-postgres15.md` both name one
document. Two documents matching the reference is an error, not a coin flip.

`Superseded-by: none` is how most ADR templates say "not yet replaced", and `none`,
`n/a`, `tbd` and `-` are read that way rather than as filenames.

A declaration line is not a claim. `Supersedes: adr-001.md` says something about
the document, so recall will not hand it back as a decision.

Pairing: a target claim is retired by the new claim with the same key. Failing
that, if the same number of claims remain on both sides, they pair in order —
which is what "this record replaces that one" means for a document that was edited
rather than rewritten. If the counts do not agree and no keys match, the write is
refused and the error names the ids to pass `--supersedes`, because pairing
unequal lists by guesswork moves live facts into history on a coin flip.

An explicit `--supersedes` or `--topic-key` outranks the declaration: a caller who
named a claim is not overruled by a line in the file.

**Where two documents disagree and neither is retired**, recall reports the
disagreement rather than picking one:

```console
$ mindpalace recall "What is the sole npm package?"
1 key(s) have sources that disagree:
  gsd.package_boundary:
    - The sole npm package is get-shit-done-cc.  (adr-0174.md)
    - Every Shared Module has exactly one hand-authored source of truth.  (adr-3524.md)

No answer is given while sources disagree.
```

### The pattern that works for decision records

Architecture decision records are a chain of decisions about one subject, written
as separate numbered files. Ingest them as documents, where the structural reader
gives each record its own claims, and retire the old one with `--supersedes` or
`--topic-key`.

### `memory_remember`

An agent can now call `memory_remember` over MCP. It is the sixteenth tool and the
first one that writes memory:

```
memory_remember(statement: str, corpus: str, key: str | None = None,
                file: str | None = None,
                supersedes_claim_id: str | None = None,
                topic_key: str | None = None)
```

The arguments and the code path are the SDK's `remember()`, so an agent and a
script cannot write memory differently. Idempotent: the identical call again
returns the same `version_id` and `event: UNCHANGED`. A changed value on the same
`key` supersedes the old one, and `supersedes_claim_id` / `topic_key` do the same
across files.

**The receipt names near-misses.** When a write lands close to claims the corpus
already holds, the result carries `related_active_claims`:

```json
{
  "corpus": "project",
  "version_id": "d76cccde8d83",
  "event": "NEW",
  "path": "memory/text-4c1e2b8a9f03.md",
  "related_active_claims": [
    {
      "claim_id": "471df2dd20ed...",
      "key": "md:adr-002-move-the-primary-datastore-a9daa0f1:0",
      "score": 1.0,
      "hint": "Possible supersession or conflict with claim 471df2dd20ed..."
    }
  ],
  "query_id": "b062424783ba"
}
```

The archive cannot tell a supersession from a contradiction, so it says which live
claims read like this one and lets the caller decide in the same turn. It is
lexical rather than semantic because `remember` deliberately runs without the
embedding model; a hint that needed a five-second model load would not appear on
the first write, which is where it is most useful.

`query_id` is the handle for `mindpalace feedback <query_id> --verdict right|wrong`.
See [runtime.md](runtime.md#the-query-log).

It takes no credential. The caller's identity is whatever
`MIND_PALACE_MCP_CREDENTIAL` resolves to, and that identity needs write access to
the corpus — a read-only credential is refused.

**Running the server.** There is no `mindpalace mcp` command. The server is an
executable on the path, `mindpalace-mcp`, installed by the `[mcp]` extra
(`pip install 'mindpalace-os[mcp]'`). It speaks JSON-RPC over stdin/stdout and
ignores `--help`, so probe it with an `initialize` request rather than a flag.

**Getting a credential.** Mint one with the CLI; the raw `mpk_...` secret is what
goes in the variable:

```console
$ mindpalace-mcp --help
mindpalace-mcp -- Mind Palace over the Model Context Protocol
...

$ mindpalace credential issue my-agent -c default -r read_write
$ export MIND_PALACE_MCP_CREDENTIAL=mpk_...
```

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

The abstention, which is what a document whose prose has no list items, numbered
entries or `Key: Value` lines gives:

```console
$ mindpalace recall "What is our incident response SLA?" --corpus agentdemo
No memory matched that question.
  NO_RELEVANT_MEMORY

A document becomes memory from its headings, list items and
`Key: Value` lines, or from an authored `claims:` block. A file
with prose and no such lines contributes nothing here. See
docs/authoring.md for both forms.
```
