"""`remember --file` must obey the same ingest roots as `ingest-repo`.

The deployment policy is `MIND_PALACE_INGEST_ROOTS`: filesystem reads happen
only inside those directories. Two surfaces enforced it -- `POST /api/ingest/repo`
and `POST /api/corpora/{name}/sync` -- and `remember(file=...)`, which reaches
`api/services/remember.py::_read`, did not. A path the deployment refused came
back through the other door.

This is the chokepoint test: `_read` is the one function every `file=` caller
routes through, so one assertion here covers the CLI, the REST route and the SDK.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, env_extra):
    env = {**os.environ, **env_extra}
    env.pop("MEMORY_TEST_DATABASE_URL", None)
    done = subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return done


def test_remember_file_refuses_a_path_outside_the_ingest_roots(tmp_path):
    """The reproducer: the same path that `ingest-repo` refuses."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("The root password is correct-horse-battery.\n")

    env = {
        "MIND_PALACE_INGEST_ROOTS": str(allowed),
        "MIND_PALACE_RUNTIME": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    corpus = "roots-bound-remember"

    # The surface that already enforced it, for contrast.
    refused = _run(["ingest-repo", str(outside), "--corpus", corpus], env)
    assert "outside MIND_PALACE_INGEST_ROOTS" in refused.stdout + refused.stderr, refused.stdout

    # The one that did not.
    allowed_write = _run(["remember", "--file", str(outside), "--corpus", corpus], env)
    shown = allowed_write.stdout + allowed_write.stderr
    assert "Remembered" not in shown, shown
    assert "outside MIND_PALACE_INGEST_ROOTS" in shown, shown


def test_remember_file_still_works_inside_the_ingest_roots(tmp_path):
    """A fix that refuses everything would pass the test above. This one fails."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    root = tmp_path / "docs"
    root.mkdir()
    doc = root / "note.md"
    doc.write_text("The cache is warm on every runner.\n")

    done = _run(
        ["remember", "--file", str(doc), "--corpus", "roots-allow-remember"],
        {
            "MIND_PALACE_INGEST_ROOTS": str(root),
            "MIND_PALACE_RUNTIME": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    )
    shown = done.stdout + done.stderr
    assert "Remembered" in shown, shown


def test_the_message_names_no_allowed_root(tmp_path):
    """A refusal that names the roots tells a caller what to aim at."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL")
    allowed = tmp_path / "secret-root-dir"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("content\n")

    done = _run(
        ["remember", "--file", str(outside), "--corpus", "roots-leak-check"],
        {
            "MIND_PALACE_INGEST_ROOTS": str(allowed),
            "MIND_PALACE_RUNTIME": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    )
    assert str(allowed) not in done.stdout + done.stderr
