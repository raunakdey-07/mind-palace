"""Two policy bypasses found by an independent reviewer, reproduced here.

Both were reported against surfaces that an audit table had already marked as
enforcing the policy. Both were real.

1. `sync_repo` never called `require_permitted`, so the SDK's `sync()` and the
   MCP `sync` tool read outside `MIND_PALACE_INGEST_ROOTS` while the CLI and the
   two REST routes did not.

2. The SDK's memory operations resolved their corpus with `write=False`, so a
   `read_only` credential created a snapshot through the SDK. The REST and MCP
   adapters pass `write=True` for write operations; the shared core did not.
   Same credential, same operation: REST 403, SDK succeeded.
"""

from __future__ import annotations

import os

import pytest


def _env(**extra):
    env = {
        **os.environ,
        "MIND_PALACE_RUNTIME": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        **extra,
    }
    env.pop("MEMORY_TEST_DATABASE_URL", None)
    return env


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_sdk_sync_refuses_a_path_outside_the_ingest_roots(tmp_path, monkeypatch):
    """The SDK and MCP `sync` are not special: they obey the same policy."""
    from mindpalace_sdk import MindPalace

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "a.md").write_text("A document outside the configured root.\n")

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))
    try:
        MindPalace("sdk-sync-roots").sync(str(outside))
    except Exception as exc:  # noqa: BLE001 - any refusal is a pass
        assert "MIND_PALACE_INGEST_ROOTS" in str(exc) or "ingest_path" in str(exc).lower(), exc
        return
    pytest.fail(f"sync({outside}) succeeded with roots={allowed}")


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_sdk_sync_still_works_inside_the_ingest_roots(tmp_path, monkeypatch):
    """A fix that refuses everything must not pass the test above."""
    from mindpalace_sdk import MindPalace

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "a.md").write_text("A document inside the configured root.\n")

    monkeypatch.setenv("MIND_PALACE_INGEST_ROOTS", str(allowed))
    summary = MindPalace("sdk-sync-allow").sync(str(allowed))
    assert summary.added >= 1


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")
def test_a_read_only_credential_cannot_snapshot_through_the_sdk(capsys):
    """The shared core must apply the same write gate the adapters apply."""
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    issued = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "credential",
            "issue",
            "ro-probe",
            "-c",
            "default",
            "-r",
            "read_only",
        ],
        cwd=root,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    secret = ""
    for line in issued.stdout.splitlines():
        if "mpk_" in line:
            secret = line.strip().split(":", 1)[-1].strip()
            break
    if not secret:
        pytest.skip("could not issue a read-only credential")

    probe = (
        "from mindpalace_sdk import MindPalace\n"
        f"c = MindPalace('default', api_key={secret!r})\n"
        "try:\n"
        "    c.memory.snapshot(corpus='default')\n"
        "    print('ALLOWED')\n"
        "except Exception as e:\n"
        "    print('REFUSED')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=root,
        env=_env(MIND_PALACE_AUTH_MODE="required"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "ALLOWED" not in done.stdout, done.stdout + done.stderr[-500:]
    assert "REFUSED" in done.stdout, done.stdout + done.stderr[-500:]
