# Retrieval reliability with a small embedder — this round's measurements

Every number below was produced by a command run in this session. The artifacts
are `retrieval-lab-dev.json`, `retrieval-lab-heldout-v1.json`,
`retrieval-lab-gitderived.json` and `retrieval-lab-calibration.json`. The lab
protocol was frozen before any reported run
(`retrieval-lab-protocol.freeze.json`, sha256
`ff61071c08dcf0d846ea6febce45f2d92b3f7789c49dd642b5dbed2f4c3e128d`).

## The headline

**Partly.** A 22.7 M-parameter embedder plus a 22.7 M cross-encoder ranks well
and separates answerable from absent almost perfectly. It cannot be made
*reliable* at useful coverage, and the reason is not the gate.

| | value |
|---|---|
| best arm | **E** — fusion plus the shipped cross-encoder |
| dev recall@1 | 0.733 |
| dev MRR | 0.852 |
| dev answerable-vs-absent AUROC | 0.9961 |
| **max coverage at ≤2% confident-wrong** | **36.4%** |
| max coverage at ≤10% confident-wrong | 45.5% |
| proposed target | ≤2% confident-wrong at ≥70% coverage |

The target is **not reachable**, and the protocol says to report the ceiling
rather than move the target. It is 36.4%, not 70%.

## What the numbers say the limit is

A logistic gate fitted on nine features — cosine top-1 and margin, BM25 top-1
and margin, fused rank, cross-encoder score and margin, whether the top passage
is claim-backed, and whether three arms agree — reaches **14.2% coverage at ≤2%
confident-wrong**. That is *worse* than simply ranking by the raw
cross-encoder score (36.4%).

So the four hand-tuned gate parameters are not the bottleneck. **The features
are.** Everything available from a small embedder, combined and weighted by a
model fitted on 202 dev rows, is worse than one number from the reranker.

## Per-slice, because the average is not the story

recall@1, dev, 202 questions (176 answerable, 26 absent):

| slice | n | A shipped | B BM25 | C semantic | D RRF | E +rerank | F passage |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 30 | 0.933 | 0.600 | 0.933 | 0.767 | **1.000** | 0.567 |
| historical | 30 | 0.967 | 0.933 | 0.967 | 0.933 | **1.000** | 0.600 |
| provenance | 26 | 0.808 | 0.731 | 0.808 | 0.731 | **1.000** | 0.846 |
| adversarial | 19 | 0.444 | 0.278 | 0.444 | 0.278 | 0.667 | **0.944** |
| multi-topic | 23 | 0.696 | 0.087 | 0.696 | 0.348 | 0.217 | **0.739** |
| temporal | 13 | 0.538 | 0.385 | 0.538 | 0.385 | **0.615** | 0.385 |
| relationship | 34 | 0.471 | 0.441 | 0.471 | 0.412 | **0.500** | 0.176 |
| abstention | 25 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

Three things fall out, and none of them is flattering to the shipped default:

1. **`relationship` is broken for every arm** (0.176–0.500). "Which service
   owns X" is the same class that produced the held-out v1 `F2` and `F9`
   failures. No ranking strategy touches it, which makes it a *representation*
   limit: the corpus does not encode ownership in a way a retriever can match.
2. **E collapses on `multi-topic`** (0.217, against 0.696 for the shipped arm)
   while being perfect on `current`, `historical` and `provenance`. The reranker
   sharpens single-answer questions and blurs multi-answer ones.
3. **F wins `adversarial` (0.944) and `multi-topic` (0.739)** and loses badly on
   `current` (0.567). Passage retrieval is not a better retriever; it is a
   *different* one, and the two fail on disjoint slices.

`conflict` has n=2 and is reported as "too few questions to report a rate"
rather than as a number.

## Why arm A and arm C are identical where it counts

At equal coverage they produce the same four-way breakdown: 100 correct, 23
wrong, 0 false answers, 53 abstained. The shipped relevance gate's topic
decomposition and `relative` filter change *which* keys rank where, but not
which questions get answered wrong.

## What F changes, and what it costs

Arm F indexes passages so body text can answer. 17 of 72 dev passages carry no
authored claim, and the dogfood's confident-wrong answer was exactly a body-text
answer. F produced **15 unbacked top-1 answers** on dev — body text winning
outright. It never promotes one to a claim, and that count is reported next to
every F result.

F is not a replacement. On the burned held-out v1 it fixes **12 of the 26**
questions the shipped system gets wrong, the most of any arm — but its
confident-wrong rate at equal coverage is 0.350 against E's 0.195.

| arm | fixes of 26 known-wrong |
|---|---:|
| F (passage) | 12 |
| C (semantic) | 9 |
| A (shipped) | 6 |
| D (RRF) | 4 |
| B (BM25) | 2 |
| any arm | 13 |
| every arm | 1 |

Only **one** of the 26 is fixed by all five. They are not variations on one
system; they fail differently.

## Two measurement errors I made and corrected

**BM25 was measured wrong.** Scoring documents as *sets* gives every term
tf = 1. On claim representations averaging 8.6 distinct terms that produces
exact score ties — three different keys all at 6.91 for "what is the current
primary datastore". Ties broken by corpus order are not a ranking. With real
term frequency, recall@1 went from **0.029 to 0.528**. The first number was an
artefact of the inputs.

**`pip install mindpalace-os` is not 13 MB.** Last round reported it as 13 MB
with no alembic. The measurement was wrong: the virtualenv activation had
silently failed and pip installed into the base container venv, so the timing
was 0 s and the `du` read an empty directory. Measured properly: **211 s and
5.9 GB**, because the wheel declares `sentence-transformers`, which pulls torch.

## What is not established

* **Sealed v4.** Decision D2 was left unresolved and the only model credential
  has a `$0.00` ceiling. No independent author exists. Nothing here is
  evidence of generalisation.
* **Arm G, embedder vs pipeline.** `BAAI/bge-small-en-v1.5` is not in the local
  HuggingFace cache and `HF_HUB_OFFLINE=1`. Whether the ceiling is the
  embedder or the pipeline is **NOT ESTABLISHED**, and the cross-encoder result
  makes the question sharper rather than answering it: E is a 22.7 M reranker,
  not a 33 M embedder, so the comparison the round wanted was not run.
* **git-derived set.** Built and frozen: 234 questions, 214 with passage gold,
  20 absent, 1399 passages from 5 tags × 3 files. Passage-level only, because
  **0 of 66** of this repository's documents carry an authored `claims:` block.
  Arm F scores `passage_recall@1 = 0.0093` on it — a real and poor result, from
  a template-shaped set that is narrow by construction.
* **L2 rebuild digest.** Not re-verified this session. The frozen
  `baseline-packs.json` and the `eval/m00675` artifact were not touched.
* **Downstream value.** No model is reachable.
