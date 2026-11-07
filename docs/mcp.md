# Connect an agent

`VERILORE_QUERY_LOG: "1"` is the flight recorder. It is off by default and
off everywhere unless you set it. Turn it on when you are dogfooding the archive
and want `verilore feedback <query_id> --verdict right|wrong|abstained_wrongly`
to grade real questions; it appends one row per call to
`~/.verilore/logs/queries.jsonl`, which holds the query text to 512 characters
plus the corpus, the ranking and the returned claim ids — never claim text. The
file is `0600` inside a `0700` directory, and nothing leaves the machine. Leave it
out of a shared or deployed configuration; `docs/security.md` has the boundary.

```jsonc
// ~/.config/opencode/opencode.json   (OpenCode v2)
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "mind-palace": {
        "type": "local",
        "command": ["verilore-mcp"],
        "environment": {
          "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/verilore",
          "VERILORE_INGEST_ROOTS": "/home/you/projects",
          "VERILORE_QUERY_LOG": "1"
        }
      }
    }
  }
}
```

OpenCode v1 puts the server directly under `mcp` instead of under `mcp.servers`,
and spells the environment block `environment` in both. From a shell:

```bash
opencode mcp add mind-palace \
  --env DATABASE_URL="$DATABASE_URL" \
  --env VERILORE_INGEST_ROOTS="$PWD" \
  -- verilore-mcp
```

```jsonc
// .mcp.json, or ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "mind-palace": {
      "command": "verilore-mcp",
      "env": {
        "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/verilore",
        "VERILORE_INGEST_ROOTS": "/home/you/projects"
      }
    }
  }
}
```

```jsonc
// Zed: settings.json, via "agent: open settings" then AI, then MCP Servers
{
  "context_servers": {
    "mind-palace": {
      "command": "verilore-mcp",
      "args": [],
      "env": {
        "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/verilore",
        "VERILORE_INGEST_ROOTS": "/home/you/projects"
      }
    }
  }
}
```

Check the connection before trusting it. `verilore-mcp` speaks JSON-RPC over
stdio, so it prints nothing on a successful start; the client is what tells you
whether it connected. `opencode mcp list` in OpenCode, or Settings, AI, MCP
Servers in Zed, and a failed server usually means a `command` that is not on the
path the client sees.

Seventeen tools are advertised. `memory_recall`, `memory_query`, `memory_explain`,
`memory_current`, `memory_history`, `memory_changes`, `memory_evidence`,
`memory_as_of`, `memory_replay`, `memory_pack`, `memory_snapshot`,
`memory_remember`, `memory_revalidate`, `list_corpora`, `search`, `sync` and `context`.

## What comes back

Every read tool returns the same object, in both `content` and `structuredContent`,
so a client that parses the text and one that reads the schema see the same answer.
Four fields are worth an agent's attention:

`match_status` is `confident`, `ambiguous` or `abstained`. Treat `ambiguous` as an
instruction to read `candidates` rather than as an error. It means the ranking
scored two claims too close to separate, which on the dev corpus is a correct
warning more often than a wrong answer.

`candidates` carries the top three with `score`, `status`, `claim_ids`,
`version_ids`, `path` and up to three verbatim `quotes`. The quotes are the point:
they are what makes a candidate checkable without trusting the score.

`related_active_claims` comes back from `memory_remember` whenever the corpus
already holds claims that read like the one being written. Each row carries
`channel`, which is `vector` for a cosine over the whole corpus or `lexical` for
token overlap. A lexical row is noisier; a vector row is not.

`memory_remember` also accepts `state` ("approved", "draft" or "rejected") and
`scope` (a relative folder such as "campaigns/spring"). With `scope`, the stored
claim key is `"<scope>/<key>"`, so the same key in two campaigns is two memories
that never supersede or conflict. A `state` or `scope` that the SDK refuses
comes back as a readable refusal (`invalid_state` / `invalid_scope`) rather than
a broken server, and `file=` cannot be combined with `state` or `scope`.

`query_id` appears on both reads and writes, and is what `verilore feedback`
grades.

## The two argument mistakes

`request` and `request.corpus` are both required, and the query goes in
`request.query`:

```json
{
  "name": "memory_recall",
  "arguments": { "request": { "corpus": "default", "query": "Which datastore?" } }
}
```

Passing `{"question": "..."}` fails with `request Field required`. Asking
`memory_history` in prose works:

```json
{
  "name": "memory_history",
  "arguments": { "request": { "corpus": "default", "query": "What is the primary datastore?" } }
}
```

It returns the same timeline as asking for `primary datastore`, because a question
is not a claim and the question words are stripped before matching.