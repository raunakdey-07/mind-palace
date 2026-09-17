# Dispatch: an evolving engineering project

These are authored demonstration sources, not claims about Mind Palace itself.
Run `python examples/memory_demo.py` from the repository root after installing the
project and applying migrations. See [the walkthrough](../MEMORY.md) for commands
and [the public contract](../MEMORY_API.md) for selectors and limits.

| Stage | Temporary working tree | Expected memory observation |
|---|---|---|
| A | All three `stage-a/*.md` files | Redis Streams; two conflicting auth claims |
| B | Replace only `architecture.md` with `stage-b/architecture.md` | Kafka supersedes Redis Streams |
| C | Replace only `architecture.md` with `stage-c/architecture.md` | Kafka managed supersedes Kafka |
| deletion | Remove only `architecture.md` | No current streaming claim; evidence survives |
| restoration | Copy stage C architecture back unchanged | RESTORED after tombstone; same document identity |

`stage-b` and `stage-c` are replacement files, **not complete directories to sync**.
The script assembles the working tree in a temporary directory, leaving these
checked-in sources untouched. Never sync this entire fixture tree: stage paths
would become separate documents instead of successive versions of the same path.

Every architecture version authors the same key, `architecture.streaming`, and a
different value. Both auth documents author `security.auth` with different values.
The exact `claim` and `evidence` text also appears in each document's body.
No claims are inferred from the surrounding engineering narrative. Validity is
left unknown rather than fabricated from the time the demo runs.

The default output is `runs/<unique-corpus>/` here (ignored by Git). It contains
canonical public SDK responses and `report.json` with actual timings and IDs.
Reports are single-run observations, not a benchmark. A failed run may leave a
partial report and committed corpus data; rerun with a new name, not by deleting
an archived corpus. The M004 append-only guards currently block corpus deletion.
