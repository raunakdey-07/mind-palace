# Project decision log

Mind Palace used for the thing a real project actually needs memory for: **why was it
decided this way?**

The decisions here are real ones from this repository's own history — the retrieval
arms that were measured and rejected, the contracts that were frozen, the scope that
was deferred. Each one was a real choice that took measurement to make, and each one
is currently scattered across `CHANGELOG.md`, `docs/STATUS.md` and `docs/research/`.

## Run it

```bash
pip install -e .
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
python examples/project-log/project_log.py
```

Nothing else. No environment variable of its own, no SQL, no internal module, and
nothing to clean up. Safe to run repeatedly — the same decisions produce the same
memory, so a second run reports `already recorded` and changes nothing.

## What it uses

`mindpalace_sdk.MindPalace` and nothing else. Every call is a public method:

| call | what the example does with it |
|---|---|
| `client.memory.remember(claim, key=...)` | one decision becomes one authoritative claim |
| `client.recall(question)` | answer, and abstain honestly when nothing was decided |
| `client.explain(question)` | the claim, its evidence, and the authoritative digest |
| `client.memory.history(corpus=..., path=...)` | what a decision said before it changed |
| `MindPalace(name=..., runtime=True)` | use the warm local runtime when one is running |

The SDK is the only import. If this file needed a change to work, the SDK would not be
usable yet — which is why it exists.

## The format

`decisions.md` is ordinary Markdown with YAML frontmatter, in exactly the shape
`mindpalace remember --file` accepts. A project can keep its decisions next to its
code, in a file a human reads and edits, and this example reads it with no parser of
its own.

Each claim carries a `key`, so writing a changed decision under the same key
supersedes the old one rather than accumulating contradictions.

## Why it is useful, concretely

The questions in the example are the ones a new contributor actually asks. Three of
the six answer from memory; the other three include one that this project never
decided, and the honest answer is:

```text
What is our incident response SLA?
  -> NO_RELEVANT_MEMORY
     (nothing invented: this project never decided that)
```

That is the property that makes a decision log worth having over `grep`: the log
answers, and it says when it does not know.
