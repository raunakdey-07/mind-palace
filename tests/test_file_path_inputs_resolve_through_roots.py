"""Every caller-supplied file path goes through the one resolver.

`require_permitted` resolves first and compares after, so a symlink that leaves
the root and a `..` that climbs out of it are both caught by the same check.
`tests/test_credential_management.py` proves the resolver does that. These prove
the *surfaces* use it -- a correct resolver nothing calls is not a control.

Two shapes of path that have been wrong before:

  - `remember --file` read any path at all (fixed; this file locks it in);
  - `ingest <path>` still reads with a bare `open()`.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, roots):
    env = {
        **os.environ,
        "MIND_PALACE_INGEST_ROOTS": str(roots),
        "MIND_PALACE_RUNTIME": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    env.pop("MEMORY_TEST_DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_remember_refuses_a_symlink_that_leaves_the_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("The production key is hunter2.\n")
    link = allowed / "innocent.md"
    link.symlink_to(secret)

    done = _run(["remember", "--file", str(link), "--corpus", "symlink-remember"], allowed)
    shown = done.stdout + done.stderr
    assert "Remembered" not in shown, shown
    assert "outside MIND_PALACE_INGEST_ROOTS" in shown, shown


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_remember_refuses_a_dotdot_that_climbs_out(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("The production key is hunter2.\n")

    escaping = allowed / ".." / "outside.md"
    done = _run(["remember", "--file", str(escaping), "--corpus", "dotdot-remember"], allowed)
    shown = done.stdout + done.stderr
    assert "Remembered" not in shown, shown
    assert "outside MIND_PALACE_INGEST_ROOTS" in shown, shown


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_remember_accepts_a_relative_path_inside_the_root(tmp_path):
    """Relative paths are the normal case; a fix that refused them would be useless."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "note.md").write_text("The cache is warm on every runner.\n")

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "remember",
            "--file",
            "note.md",
            "--corpus",
            "relative-remember",
        ],
        cwd=allowed,
        env={
            **os.environ,
            "MIND_PALACE_INGEST_ROOTS": ".",
            "MIND_PALACE_RUNTIME": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            # The process runs from the root directory under test, so the
            # repository has to be importable by name.
            "PYTHONPATH": ROOT,
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    shown = done.stdout + done.stderr
    assert "Remembered" in shown, shown


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_ingest_refuses_a_path_outside_the_root(tmp_path):
    """`ingest` read the file with a bare `open()` and posted its contents."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("The production key is hunter2.\n")

    done = _run(["ingest", str(outside)], allowed)
    shown = done.stdout + done.stderr
    assert "outside MIND_PALACE_INGEST_ROOTS" in shown, shown


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_ingest_refuses_a_symlink_that_leaves_the_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("The production key is hunter2.\n")
    link = allowed / "innocent.md"
    link.symlink_to(secret)

    done = _run(["ingest", str(link)], allowed)
    shown = done.stdout + done.stderr
    assert "outside MIND_PALACE_INGEST_ROOTS" in shown, shown


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_ingest_accepts_a_file_inside_the_root(tmp_path):
    """The refusal must not become a blanket refusal."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "note.md").write_text("The cache is warm on every runner.\n")

    done = _run(["ingest", str(allowed / "note.md")], allowed)
    shown = done.stdout + done.stderr
    # `ingest` is an HTTP client, so a refused path and a missing server are
    # different failures. Only the refusal text is a failure here.
    assert "outside MIND_PALACE_INGEST_ROOTS" not in shown, shown
