"""A `claims:` block that YAML rejects must say so, and must not leak its content.

`parse_markdown` has produced a `frontmatter_unparseable` warning with a line
number since the first-hour audit. The document path never asked for it, so an
ingested document whose claims were silently discarded reported
`no_claims_declared` -- which reads as "you wrote no claims" when the truth is
"your claims were thrown away".

The message names the path and a reason code. It must never carry the document's
content: this line is printed to whoever ran the sync, and a rejected block is
often rejected *because* it contains something sensitive or half-written.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A backtick cannot start a YAML plain scalar. Inline code is ordinary in
# Markdown prose, so this is the shape people actually write.
BACKTICKED = """---
title: Bad quoting
claims:
  - key: a.b
    value: v
    claim: c
    evidence: `get-shit-done-cc` is the sole npm package.
---

# Bad quoting

The sole npm package is get-shit-done-cc.
"""

COLONED = """---
title: Bad quoting
claims:
  - key: a.b
    value: v
    claim: c
    evidence: note: the value is X
---

# Bad quoting

note: the value is X
"""

SECRET = "correct-horse-battery-staple"


def _ingest(tmp_path, body: str, corpus: str) -> str:
    """Run the command as a user runs it, and return what they are shown.

    `ingest-repo` has no --json, so this asserts on the text. That is the point:
    the text is where a wrong report does its damage.
    """
    d = tmp_path / "docs"
    d.mkdir(exist_ok=True)
    (d / "bad.md").write_text(body)
    # Pinned to the session database explicitly. Inherited, the subprocess would
    # see MEMORY_TEST_DATABASE_URL and write to the shared one instead, which is
    # both the wrong target for a test and the reason a re-run could disagree
    # with the first.
    env = {
        **os.environ,
        "MIND_PALACE_INGEST_ROOTS": str(tmp_path),
        "MIND_PALACE_RUNTIME": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    env.pop("MEMORY_TEST_DATABASE_URL", None)
    done = subprocess.run(
        [sys.executable, "-m", "cli.main", "ingest-repo", str(d), "--corpus", corpus],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode in (0, 1), done.stderr[-2000:]
    # Per-document problems go to stderr, where the CLI has always put them, and
    # the summary line goes to stdout. A sync that discarded claims exits 1, so
    # the two streams are read together.
    return done.stdout + done.stderr


@pytest.mark.parametrize(
    ("body", "case"),
    [(BACKTICKED, "backtick"), (COLONED, "colon")],
)
def test_an_unparseable_claims_block_is_reported_as_such(tmp_path, body, case):
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    shown = _ingest(tmp_path, body, f"unparseable-{case}")

    # Not `no_claims_declared`: that tells the author the wrong thing.
    assert "no_claims_declared" not in shown, shown
    # Path and reason code, on one line a reader can act on.
    assert "bad.md: frontmatter_unparseable" in shown, shown


def test_the_warning_carries_no_document_content(tmp_path):
    """The reason must be safe to print, paste into an issue, or log."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    body = BACKTICKED.replace("get-shit-done-cc", SECRET)
    shown = _ingest(tmp_path, body, "unparseable-secret")

    assert "frontmatter_unparseable" in shown, shown
    assert SECRET not in shown


def test_a_clean_document_is_not_warned_about(tmp_path):
    """A warning that fires on healthy documents is a warning nobody reads."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    d = tmp_path / "docs"
    d.mkdir()
    (d / "ok.md").write_text(
        "---\ntitle: Fine\nclaims:\n  - key: a.b\n    value: v\n"
        '    claim: The cache is warm.\n    evidence: "The cache is warm."\n---\n\n'
        "# Fine\n\nThe cache is warm.\n"
    )
    env = {
        **os.environ,
        "MIND_PALACE_INGEST_ROOTS": str(tmp_path),
        "MIND_PALACE_RUNTIME": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    env.pop("MEMORY_TEST_DATABASE_URL", None)
    done = subprocess.run(
        [sys.executable, "-m", "cli.main", "ingest-repo", str(d), "--corpus", "clean-ok"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    shown = done.stdout + done.stderr
    assert "frontmatter_unparseable" not in shown
    assert "no_claims_declared" not in shown
