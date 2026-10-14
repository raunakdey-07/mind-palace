# Quickstart

Commands only. Each one is the command, not a description of it. The README
explains why; this is for the person who already read it and wants the list.

```bash
# 1. Install. Expect 5.9 GB and 79-202 s; torch pulls the CUDA runtime.
git clone https://github.com/raunakdey-07/mind-palace.git && cd mind-palace
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# 2. Start PostgreSQL. Three values compose will not invent; copy them from
#    .env.example if you would rather set them in a file.
export MIND_PALACE_RUNTIME_PASSWORD=choose-a-local-password
export MIND_PALACE_CURSOR_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export MIND_PALACE_INGEST_ROOTS="$PWD"
docker compose up -d postgresql

# 3. Point at it and apply the schema.
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head

# 4. Write one fact and ask about it.
mindpalace init
mindpalace remember "Production uses PostgreSQL." --key datastore
mindpalace recall "What database does production use?"
```

The first `recall` downloads the embedding model, about 90 MB, once. After that
it works offline. To never download it, set `MIND_PALACE_LEXICAL=1` before step 4
— see [runtime.md](runtime.md#model-free-lexical-mode).

## Getting a document in

`remember` is for facts. Documents need a `claims:` block in their frontmatter,
and that is the whole authoring interface — [authoring.md](authoring.md) is the
reference, and it is the third thing worth reading.

```bash
mkdir -p ./docs
cat > ./docs/deployment.md <<'EOF'
---
title: Deployment architecture
claims:
  - key: architecture.datastore
    value: PostgreSQL
    claim: The primary datastore is PostgreSQL.
    evidence: The primary datastore is PostgreSQL.
---

# Deployment architecture

The primary datastore is PostgreSQL. It runs in three availability zones.
EOF

mindpalace ingest-repo ./docs
mindpalace recall "What is the primary datastore?"
```

Both commands run in-process. Nothing needs a server started.

## When it answers nothing

A document with no `claims:` block is stored and searchable but answers nothing:
recall reads authored claims, not prose. Ingest one and it comes back named in the
output as `no_claims_declared`. `evidence` must appear verbatim in the document —
[authoring.md](authoring.md) has the rejection messages and what causes each one.

## What to read next

Nothing. That is the whole first hour. When you need more, the
[README](../README.md#documentation) table says which one.