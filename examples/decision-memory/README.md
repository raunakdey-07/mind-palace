# Decision memory

The smallest useful Mind Palace application: an agent that remembers decisions,
answers questions about them, shows its basis, and hands you a receipt you can
verify without this program running.

```bash
python examples/decision-memory/decision_memory.py
```

Everything below is what that script does. It is here so you can read the whole
product in one file, not as a framework to copy.

## What it demonstrates

| Step | What it shows |
|---|---|
| `remember` | A statement becomes authoritative memory, through the same archive a synced document uses. |
| `recall` | An answer, with its source and the time it was true. |
| `explain` | The exact characters that support it, and what replaced it. |
| `history` | A real supersession chain, oldest first. |
| `receipt` | What was returned, and the digest of the whole response. |
| `verify` | Offline verification with no database, no model and no network. |

## Requirements

A running Mind Palace database. `mindpalace init` prints the two commands:

```bash
docker compose up -d postgresql
python -m alembic -c migrations/alembic.ini upgrade head
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
```

No embedding model, no API key, no LLM.