# Mind Palace Examples

Minimal, reproducible examples of using Mind Palace as AI memory infrastructure.

All examples assume a running PostgreSQL+pgvector database (see the README
Quick Start) and `DATABASE_URL` set in your environment.

---

## Example A — Documentation memory

A small documentation tree, ingested and queried.

```bash
python - <<'EOF'
from mindpalace_sdk import MindPalace

mp = MindPalace("product-docs")
summary = mp.sync("examples/docs")
print(summary)

pack = mp.context("How does authentication work?", budget_tokens=2000)
print(pack.context)        # bounded evidence text for your prompt
for s in pack.sources:
    print(f"source: {s.title} ({s.path})")
print(f"tokens: ~{pack.token_estimate}, truncated: {pack.truncated}")
EOF
```

## Example B — Repository memory

Point Mind Palace at any directory containing Markdown.

```bash
python - <<'EOF'
from mindpalace_sdk import MindPalace

mp = MindPalace("repo-memory")
mp.sync("./")   # or any repository path with .md files

pack = mp.context("Where is database initialization handled?", budget_tokens=4000)
print(pack.context)
EOF
```

## Example C — Research corpus

Papers and notes.

```bash
python - <<'EOF'
from mindpalace_sdk import MindPalace

mp = MindPalace("research")
mp.sync("~/notes/papers")   # adjust to your path

pack = mp.context("Which methods were used for class imbalance?", budget_tokens=4000)
print(pack.context)
print([s.title for s in pack.sources])
EOF
```

## Example D — MCP integration

Connect an MCP-compatible client (Claude Desktop, any MCP host):

```json
{
  "mcpServers": {
    "mind-palace": {
      "command": "/path/to/your/venv/bin/python",
      "args": ["-m", "mcp_server"],
      "env": {
        "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/mindpalace"
      }
    }
  }
}
```

The model then has these tools available:

- `context(query, corpus, budget_tokens)` — model-ready context with attribution
- `search(query, corpus, k)` — raw ranked chunks
- `sync(corpus, path)` — synchronize a corpus with a directory
- `list_corpora()` — list available corpora
