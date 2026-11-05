"""Run every python block in docs/integration/guide.md against a live server.

The guide is documentation with executable blocks: each ```python block is
extracted and exec()'d in order in one shared namespace, so later blocks may
use names defined earlier. Shell blocks (```bash) are not run; instead every
shell command in the guide must appear verbatim in .github/workflows/ci.yml
or the README quickstart, and this test checks that.

The live server is module-scoped (tests/live_server.py); MIND_PALACE_URL,
BRAND_MEMORY_KEY and BRAND_CORPUS are set from the fixture before the blocks run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from cli.main import app  # noqa: E402
from live_server import live_server  # noqa: E401,F401,F811  module-scoped server fixture
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "integration" / "guide.md"

_PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
_SHELL_BLOCK_RE = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)

# Rich (which typer uses for --help) reads terminal width from os.environ["COLUMNS"].
# CliRunner's env= kwarg does not update os.environ, so set it directly to avoid
# rich truncating option names in narrow CI terminals.
_OLD_COLUMNS = os.environ.get("COLUMNS")
os.environ["COLUMNS"] = "200"

runner = CliRunner()


def _extract_blocks(text: str, pattern: re.Pattern[str]) -> list[str]:
    return pattern.findall(text)


def test_guide_python_blocks_run(live_server, monkeypatch):  # noqa: F811
    """Every python block in the guide executes in order against live_server."""
    guide = GUIDE.read_text(encoding="utf-8")
    blocks = _extract_blocks(guide, _PYTHON_BLOCK_RE)
    assert blocks, "the guide must contain python blocks"

    monkeypatch.setenv("MIND_PALACE_URL", live_server.url)
    monkeypatch.setenv("BRAND_MEMORY_KEY", live_server.keys["acme"])
    monkeypatch.setenv("BRAND_CORPUS", live_server.corpora["acme"])

    namespace: dict[str, object] = {}
    for i, block in enumerate(blocks, start=1):
        exec(block, namespace)  # noqa: S102 -- the guide blocks are the test

    # recommended_budget is defined by step 7.
    assert "recommended_budget" in namespace


def test_guide_shell_commands_appear_in_ci_or_readme():
    """Every shell command in the guide appears verbatim in ci.yml or README."""
    guide = GUIDE.read_text(encoding="utf-8")
    shell_blocks = _extract_blocks(guide, _SHELL_BLOCK_RE)
    assert shell_blocks, "the guide must contain shell blocks"

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    deployment_text = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    combined = ci_text + readme_text + deployment_text

    missing = []
    for block in shell_blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("mindpalace "):
                # Mind Palace CLI commands are checked via --help.
                tokens = line.split()[1:]
                # The command path is every token before the first --flag;
                # everything after is a flag or its value.
                path_tokens = []
                for t in tokens:
                    if t.startswith("--"):
                        break
                    path_tokens.append(t)
                result = runner.invoke(app, path_tokens + ["--help"])
                assert result.exit_code == 0, f"--help failed for: {line}"
                for token in tokens:
                    if token.startswith("--"):
                        assert (
                            token in result.output
                        ), f"flag {token} missing from --help for: {line}"
            else:
                # All other commands must appear verbatim as a whole line.
                if line not in combined:
                    missing.append(line)

    assert (
        not missing
    ), "these shell commands from the guide do not appear in ci.yml or README:\n" + "\n".join(
        missing
    )
