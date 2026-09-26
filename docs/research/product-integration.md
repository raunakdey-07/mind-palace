# Integration cost, measured

## Result

| Path | Lines | Semantic stack on import |
|---|---:|---|
| Plain script, one question | 7 | none |
| FastAPI service, one endpoint | 15 | none |
| Live query against real PostgreSQL | 27 ms | status=resolved, 1 claim, 1 evidence |

Reproduce: `venvmp/bin/python scripts/benchmark/m0116_integration.py`. Artifact:
`docs/performance/m0116-integration.json`.

The SDK import probe confirms that `mindpalace_sdk` pulls in no `torch`,
`sentence_transformers` or `transformers`.

## The whole script

```python
import os

from mindpalace_sdk import MindPalace
from memory_pack import MemoryPack

memory = MindPalace("integration-demo", base_url=os.environ["MP_BASE_URL"])
raw = memory.memory.query(corpus="integration-demo", query=QUESTION)
pack = MemoryPack.from_json(raw.canonical_json())

print(pack.status)
print(len(pack.current), "current", len(pack.conflicts), "conflict")
```

## Concepts a developer must already hold

Three:

1. a corpus is a named namespace the memory is written to
2. a question in natural language
3. the pack holds current, historical and conflicting memory, with evidence

That is the floor, and it is the right floor. Concepts the developer does *not*
need: the database schema, embeddings, RRF, rerankers, MCP, claim rows, evidence
offsets, snapshots, or what a budget unit is.

## What still costs the developer something

**Status is derived, not stored.** `MemoryResponse` carries `constraints` and
`truncated` but no `status`. To ask "did this resolve, conflict, or find
nothing", a caller has to know about `memory_pack.py` and wrap the response. The
information exists in `constraints`, so nothing is lost, but the default path
makes a developer look in the wrong place. This is the clearest remaining
developer-experience gap and it is small: a `status` derived on the response
would remove the wrap.

**The reader is a separate file.** That is deliberate, because the whole point
is that a consumer can copy one dependency-free file into another project. It
also means an application that wants the structure imports two things rather
than one. Acceptable, and the reason is stronger than the cost.

**A corpus name and a database URL are both needed.** The corpus is a namespace
and the URL is transport. For a single local corpus that is one piece of
configuration too many, and it is the strongest candidate for a default.

## What the measurement does not show

This is one question against one small corpus. It says nothing about how a
developer handles a conflict, a large corpus, or a model that is not yet
downloaded. Those need a longer session than a line count can capture, and
inventing a number for them would be theatre.

## Comparison, carefully

MemPalace reports a 96.6% R@5 on LongMemEval. That is a different dataset, task
and metric from anything here, and it is not comparable. What can be said is
narrower: Mind Palace's answer is a bounded object that names its own state and
carries an exact quote with character offsets, and the consumer above can tell
resolved from conflicting from absent without reading the documentation. Whether
that is worth more than a higher recall number on someone else's benchmark is
not something this measurement decides.
