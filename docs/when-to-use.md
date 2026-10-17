# When to use Mind Palace, and when not to

One page, for somebody deciding whether to install this rather than read the rest
of the documentation. Every number here is measured; the command that produced
each one is named.

## Use it when the answer has to be right, and the cost of being wrong is real

The property Mind Palace is built around is that it declines. On 157 questions
written by someone other than the authors, it answered when it should have
declined **once**:

| Outcome | held-out v1 (n=157) | frozen benchmark (n=60) |
|---|---:|---:|
| correct | 116 (73.9%) | 37 (61.7%) |
| abstained when the archive had an answer | 11 (7.0%) | 5 (8.3%) |
| answered wrong | 14 (8.9%) | 8 (13.3%) |
| abstained correctly | 15 (9.6%) | 10 (16.7%) |
| **answered when it should have abstained** | **1** | **0** |

```bash
python scripts/quality_frame.py     # reads the two frozen artifacts, writes the JSON
```

That is a good fit when:

* **The facts change over time and you need to know what is true now.** Every
  memory carries validity and supersession, so `--as-of` answers what was true
  then, and the old answer stays readable afterwards.
* **Two records disagree and you need to know that.** Two live claims are
  reported as a conflict, not silently merged.
* **Somebody has to be able to check the answer.** `explain` returns the exact
  character span of the archived text that supports a claim; `receipt` returns a
  digest that verifies offline with no database, no model and no network.
* **Your users will act on an answer without re-reading your source.** That is the
  case the one confident-wrong answer above is the risk in, and one in 157 is a
  risk you can state to a reviewer.

## Do not use it when

* **You need answers right now, every time.** Roughly one held-out question in six
  is a product limitation: 11 where it declined something the archive knew, 14
  where it answered something wrong. If your interface cannot show "I don't know",
  this is the wrong tool — the abstention is the feature, and hiding it makes the
  wrong answers worse.
* **Two users must not see each other's memory inside one corpus.**
  [Per-resource authorization is withdrawn](withdrawn/README.md). A corpus is a
  *namespace*; every credential that can reach a corpus can reach every document
  in it. The supported answer is **one corpus per confidentiality boundary**, and
  for mutually distrusting tenants, one database per tenant.
* **You are storing data you must be able to prove you deleted.** Deleting a
  corpus removes it from the database. It does **not** remove content from
  backups, replicas or snapshots you took elsewhere, and cryptographic erasure is
  **not implemented**. See
  [deletion and erasure](deletion-and-erasure.md).
* **You want a chat interface.** There is a retrieval layer, a claim model and a
  temporal model under it, and an application that generates prose is expected to
  sit on top. See [What it does not do](../README.md#what-it-does-not-do).

## The measured limits worth knowing before you commit

### Keyed conflict detection, and nothing more

`--key` is how you say "these two statements are about the same fact", and it is
the only conflict detection there is. Two records that are about the same fact and
that you did not give the same key are two claims, and they will both be reported
as current. There is no fuzzy or inferred supersession. On the frozen benchmark
that shows up as **10 of 60 questions missing a subject key** — the largest single
contributor to the 8 wrong answers above.

### Narrow relationship reasoning

The weakest category is asking *why* a decision was made, and asking for a rule or
a release rather than a fact: 19 of the 26 held-out failures are lexical ranking on
"what reasoning led to picking the present datastore". Asking for what is true
works. Asking for the reasoning behind it, phrased indirectly, does not.

### Latency depends on a model you did not choose

The first `recall` downloads an embedding model (about 90 MB) and builds an ONNX
session, which takes roughly 1.4 seconds; afterwards the warm runtime brings it to
about 0.3 s. `MIND_PALACE_LEXICAL=1` removes the model entirely, at the cost of
needing shared vocabulary between question and record.

The 6-second first recall in older notes was the `sentence-transformers` import, and
that is now the `[torch]` extra. If you install it you get those timings back, and
nothing else changes: the two backends produce the same vectors.

### Not a security product

Verification establishes integrity and recorded provenance. It does **not**
establish that a source was factually correct, and it does not establish
authenticity unless a digest is pinned out of band. Access control is real but it
is a boundary, not a defence against a compromised application process — see
[security.md](security.md), which says exactly what a compromised process gets.

## If you decide to use it

Follow the README literally, in this order:

1. [The five-minute path](../README.md#the-five-minute-path) — install, `init`,
   `remember`, `recall`, `explain`.
2. [What it does not do](../README.md#what-it-does-not-do) — read before you build.
3. If more than one user: [deployment and authentication](deployment.md) **first**.
   The defaults are single-user and are documented as such.
4. If you are storing anything regulated: [deletion and erasure](deletion-and-erasure.md).

Then run [`integration_conformance.py`](../integration_conformance.py) against your own
installation. It is a checklist, not a test suite, and it includes the negative
checks.