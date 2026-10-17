"""The MCP client configs in `docs/mcp.md` have to parse.

Three clients, three shapes, and a doc that drifts from any of them is worse than no
doc: the agent fails to connect and the reader has no way to tell whether the
server is broken or the snippet is. The shapes themselves are the client's, not
ours, so this pins the two things that can rot here -- malformed JSON and a key the
server does not actually implement.
"""

import json
import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "docs" / "mcp.md"

#: `jsonc` blocks are JSON with `//` comments. Stripping every `//` would eat the
#: scheme out of a URL, so comments are recognised only at the start of a line.
_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def blocks() -> list[tuple[str, dict]]:
    text = DOC.read_text()
    out = []
    for raw in re.findall(r"```(?:jsonc|json)\n(.*?)```", text, re.S):
        out.append((raw, json.loads(_COMMENT.sub("", raw))))
    return out


def server_entries(body: dict) -> list[dict]:
    """The server definitions in one snippet, whichever nesting the client uses.

    OpenCode v1 puts them directly under `mcp`; v2 nests one more level under
    `mcp.servers`; Claude Code and Zed use `mcpServers` and `context_servers`.
    """
    for key in ("mcp", "mcpServers", "context_servers"):
        if key not in body:
            continue
        block = body[key]
        if isinstance(block.get("servers"), dict):
            block = block["servers"]
        return [v for v in block.values() if isinstance(v, dict)]
    return []


def test_the_document_has_the_three_client_shapes():
    found = {}
    for raw, body in blocks():
        for key in ("mcp", "mcpServers", "context_servers"):
            if key in body:
                found.setdefault(key, body[key])
    assert set(found) == {"mcp", "mcpServers", "context_servers"}, sorted(found)


def test_every_snippet_names_the_server_and_its_environment():
    """Each of the three clients is configured, and each bounds the agent's writes.

    `MIND_PALACE_INGEST_ROOTS` is what stops `sync` and `memory_remember --file`
    reaching outside the repository, so a snippet without it documents a server with
    wider access than the reader probably intended.
    """
    seen = 0
    for raw, body in blocks():
        for server in server_entries(body):
            command = server.get("command")
            assert command, raw
            env = server.get("environment") or server.get("env") or {}
            assert "DATABASE_URL" in env, raw
            assert "MIND_PALACE_INGEST_ROOTS" in env, raw
            seen += 1
    assert seen == 3, f"expected one snippet per client, found {seen}"


def test_opencodes_command_is_an_argument_array():
    """`command` is an array in OpenCode and a string elsewhere. Getting it wrong
    starts the server with no arguments and a confusing error."""
    for _raw, body in blocks():
        if "mcp" not in body:
            continue
        for server in body["mcp"].get("servers", {}).values():
            assert isinstance(server["command"], list), server
            assert server["type"] == "local", server


@pytest.mark.parametrize("qid", ["memory_recall", "memory_history"])
def test_the_argument_examples_name_request_and_corpus(qid):
    text = DOC.read_text()
    assert f'"name": "{qid}"' in text
    for raw, body in blocks():
        if raw and f'"{qid}"' in raw:
            request = raw[raw.index(f'"{qid}"') :]
            assert '"request"' in request
            assert '"corpus"' in request
            return
    pytest.fail(f"no example for {qid} in {DOC}")


def test_every_tool_the_document_advertises_actually_exists():
    """A doc that lists a tool the server does not have costs a debugging session."""
    import asyncio

    import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    tools = tools.tools if hasattr(tools, "tools") else tools
    live = {t.name for t in tools}
    text = DOC.read_text()
    section = text[text.index("Sixteen tools are advertised") :]
    section = section[: section.index("## What comes back")]
    named = set(re.findall(r"`(memory_\w+|list_corpora|search|sync|context)`", section))
    assert named, "no tool names found in the advertisement paragraph"
    missing = named - live
    assert not missing, f"documented but not served: {sorted(missing)}"
    assert len(live) == 16, sorted(live)
    # Every read tool's schema must carry the diagnostics the doc describes.
    recall = next(t for t in tools if t.name == "memory_recall")
    properties = (recall.output_schema or {}).get("properties", {})
    for field in ("match_status", "match_margin", "candidates"):
        assert field in properties, sorted(properties)
    remember = next(t for t in tools if t.name == "memory_remember")
    inputs = (remember.input_schema or {}).get("properties", {})
    for field in ("supersedes_claim_id", "topic_key", "corpus"):
        assert field in inputs, sorted(inputs)
