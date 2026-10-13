# Held-out v3 — written, frozen, and NOT independent

`heldout-v3-freeze.json`, 45 questions, dataset hash
`d52545673ce864a9d774417085dc05f1a1d1df9be5b625107d088d42866175fb`.

**Unscored.** Scoring happens once, at one commit, by a scorer that has not read
retrieval code. Any retrieval change tuned against this set is a development
result and must be reported as one.

## Why the name is v3

`heldout-v2-freeze.json` already exists: 32 questions, frozen 2026-10-04 at
commit `01980f5`, and already scored. Issuing a second set called "v2" would make
the identifier mean two things.

## Why it is not a clean held-out set

**It was written by the same author as the system, from the same session as the
dogfood that found the bugs it asks about.** That is a conflict of interest, not
a formality: I know which questions the system fails, because I read the failure
labels. Even without reading a per-question outcome, knowing that "why did the
team move off the earlier datastore" was a measured weakness makes it a poor
unseen question.

Concretely, the category mix is derived from the failure taxonomy I had already
read:

| category | n | why it is here |
|---|---:|---|
| why_decision | 4 | 19 of 26 held-out v1 failures were lexical ranking on "what reasoning led to X" |
| near_miss | 4 | the lexical matcher requires every query token in one claim |
| scope_confusion | 2 | answered from the wrong document |
| absence | 6 | the property that matters most, and the one most likely to be over-fitted to |
| multi_topic | 4 | 0/19 on the development set |
| the rest | 25 | baseline coverage |

So this set is **not independent of the dogfood**, and any number it produces is
a development result even on its first scoring.

## What would make a clean set

Two authors from model families other than the one that built the system, each
given the README and the corpus and nothing else, plus a human if available.
None was reachable: the only model credential on this machine has a hard ceiling
of `$0.00`, and there are no human authors here. See
[cold-reader/NOT-RUN.md](cold-reader/NOT-RUN.md).

`heldout-v4-freeze.json` does not exist. The spec for it is in
[downstream-eval-protocol.md](downstream-eval-protocol.md) §3.
