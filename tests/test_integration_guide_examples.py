"""Run every python block in docs/integration/guide.md against a live server.

The guide is documentation with executable blocks: each ```python block is
extracted and exec()'d in order in one shared namespace, so later blocks may
use names defined earlier. Shell blocks (```bash) are not run; instead every
shell command in the guide must appear verbatim in .github/workflows/ci.yml
or the README quickstart, and this test checks that.

The live server is module-scoped (tests/live_server.py); MIND_PALACE_URL and
BRAND_MEMORY_KEY are set from the fixture before the blocks run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from live_server import live_server  # noqa: F401,F811  module-scoped server fixture

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "integration" / "guide.md"

_PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
_SHELL_BLOCK_RE = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)


def _extract_blocks(text: str, pattern: re.Pattern[str]) -> list[str]:
    return pattern.findall(text)


def test_guide_python_blocks_run(live_server):  # noqa: F811
    """Every python block in the guide executes in order against live_server."""
    guide = GUIDE.read_text(encoding="utf-8")
    blocks = _extract_blocks(guide, _PYTHON_BLOCK_RE)
    assert blocks, "the guide must contain python blocks"

    os.environ["MIND_PALACE_URL"] = live_server.url
    os.environ["BRAND_MEMORY_KEY"] = live_server.keys["acme"]

    namespace: dict[str, object] = {}
    for i, block in enumerate(blocks, start=1):
        exec(block, namespace)  # noqa: S102 — the guide blocks are the test

    # The last block defines recommended_budget; it must be usable.
    assert "recommended_budget" in namespace, "last block must define recommended_budget"


def test_guide_shell_commands_appear_in_ci_or_readme():
    """Every shell command in the guide appears verbatim in ci.yml or README."""
    guide = GUIDE.read_text(encoding="utf-8")
    shell_blocks = _extract_blocks(guide, _SHELL_BLOCK_RE)
    assert shell_blocks, "the guide must contain shell blocks"

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    combined = ci_text + readme_text

    missing = []
    for block in shell_blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if (
                line.startswith("docker compose")
                or line.startswith("python -m alembic")
                or line.startswith("psql")
                or line.startswith("mindpalace ")
            ):
                prefix = " ".join(line.split()[0:4])
                if prefix not in combined:
                    missing.append(line)

    assert (
        not missing
    ), "these shell commands from the guide do not appear in ci.yml or README:\n" + "\n".join(
        missing
    )
