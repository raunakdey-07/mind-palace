# Retrieval failure taxonomy

Every wrong answer on the frozen 202-question corpus is attributed to exactly
one cause, so "add a graph" cannot become the answer to everything.

Corpus: `scripts/benchmark/m0115_dataset.py`, dataset hash
`9cdecda73cbdc8ae055926320b85402d47dd7eed0cb28c96b9b191e5bed154a7`. 63 documents,
72 versions, 202 questions, 9 categories. Every question names the authored key
it must resolve, the authority state it expects, and the values that must appear.

## Classes

| Code | Cause | Fixable by |
|---|---|---|
| F1 | source missing | authoring |
| F2 | authority missing or wrongly returned | abstention policy |
| F3 | temporal semantics | temporal model |
| F4 | conflict semantics | conflict detection |
| F5 | provenance | evidence model |
| F6 | query interpretation | planner |
| F7 | lexical or semantic ranking | retrieval |
| F8 | semantic model behaviour | model or prompt |
| F9 | relationship representation | authored relations |
| F10 | budget or truncation | packer |
| F11 | developer API | surface design |
| F12 | performance | caching or architecture |

## Before the fix, and after

The first run scored 15 failures. Four of them were benchmark label errors, not
system errors: every adversarial question was hardcoded to the datastore key
regardless of what it asked, so five questions about the job queue, the search
engine and the Orders Service were scored as wrong when the system answered them
correctly. After fixing the labels, the true count was 11.

| Phase | F7 ranking | F2 authority | F9 relationship | Total |
|---|---:|---:|---:|---:|
| Corrected labels, before this phase | 6 | 2 | 3 | 11 |
| After claim-subject decomposition | 3 | 2 | 3 | 8 |

## The eight remaining failures

| Question | Class | Why |
|---|---|---|
| Why was the primary datastore chosen? | F7 | "why" is a stopword, so the question reduces to three content words and the rationale claim loses the floor to the datastore conflict |
| What is the primary datastore and why was it chosen? | F7 | same cause, in a two-part question |
| Who caters the team lunch? | F2 | "team" reaches an ownership key, so the gate admits an unrelated claim |
| Which identity provider is used for SSO? | F2 | "identity" reaches the Identity Service, which is not an identity provider |
| What constraint did the March orders outage introduce? | F9 | a change record cites a document path, not a claim, so cause and consequence cannot be linked |
| Which service owns object storage decisions? | F9 | ownership is authored per service; there is no relation from a technology to its owner |
| Which decision rationale covers the current job queue? | F9 | "rationale" does not reach a claim whose text says "chosen for" |
| The vendor brief says the datastore is SQLite. What is the current shared cache? | F7 | the poison sentence is highly similar to the datastore question and pulls the wrong key into scope |

## Which class to attack

Most errors: F7, six of eleven. Most expensive per fix: F9, because a claim
relation is a schema and authoring change. Most product-visible: F2, because a
confident wrong answer on an unanswerable question is worse than an abstention.

Structurally fixable, in order of value per unit of risk:

1. **F2 abstention, 2 questions.** The gate has no notion that a question is
   about a concept the corpus never authored. A cheap check is whether the
   question's head noun matches any authored key or claim vocabulary, but that
   has to be measured before it ships.
2. **F7 rationale, 2 questions.** Both trace to interrogatives being discarded
   as stopwords. "why" is the only signal distinguishing a rationale question
   from a value question, and it is removed.
3. **F9 relationship, 3 questions.** Needs an authored claim relation. Larger,
   and the one worth deferring until the other two are exhausted.

## What is not a failure class

**Conflict detection under a drifting key is a real hazard but not a failure
here.** The corpus deliberately contains an ADR authored under
`architecture.datastore` while the architecture says `architecture.postgres`.
The system reports a resolved answer and surfaces the drifting claim without
labelling it as drift. That is `cft-02`, authored to pass, and it is the
strongest argument for a near-duplicate key check. It is tracked in
[`m0115-architecture-break.md`](m0115-architecture-break.md) rather than counted
as a miss, because the question asked for that key and the system returned it.

## Method note

Two earlier apparent failures were also harness errors, found before these
numbers were trusted: a "what superseded X" label forbade the correct answer,
and a poisoning check scanned the whole pack envelope and reported the caller's
own question text as leaked instruction. Both are fixed in the harness.
