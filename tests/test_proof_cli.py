"""M014.1: the proof workflow through the real CLI and the real packaging.

These are the tests that would have caught the packaging boundary failure this
project already suffered once with ``memory_pack``: code that works in the source
tree but is absent from the built distribution.

Nothing here imports FastAPI, SQLAlchemy, asyncpg, torch or the database layer. If
a future change makes verification depend on the runtime, these fail.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from memory_pack import MemoryPack
from memory_proof import build_proof
from memory_proof_cli import EXIT_BAD_INPUT, EXIT_OK, EXIT_REJECTED

ROOT = Path(__file__).resolve().parents[1]

STANDALONE_MODULES = ("memory_pack.py", "memory_proof.py", "memory_proof_cli.py")

#: A pack with a real supersession chain: c-old was valid until c-new replaced it.
PACK_FIXTURE = {
    "schema_version": 1,
    "query": "What datastore did production use?",
    "corpus": "demo",
    "state": {"as_of": None, "valid_at": "2025-06-01T00:00:00+00:00"},
    "current_memories": [
        {
            "id": "c-new",
            "key": "architecture.postgres",
            "value": "PostgreSQL",
            "claim": "The primary datastore is PostgreSQL.",
            "status": "CURRENT",
            "version_id": "v-new",
            "path": "docs/tech/postgres.md",
            "observed_at": "2025-06-01T00:00:00+00:00",
            "valid_from": "2025-06-01T00:00:00+00:00",
            "valid_until": None,
            "supersedes_id": "c-old",
            "evidence_ids": ["e-new"],
        }
    ],
    "historical_memories": [
        {
            "id": "c-old",
            "key": "architecture.postgres",
            "value": "SQLite",
            "claim": "The primary datastore is SQLite.",
            "status": "SUPERSEDED",
            "version_id": "v-old",
            "path": "adrs/adr-011.md",
            "observed_at": "2025-01-01T00:00:00+00:00",
            "valid_from": "2025-01-01T00:00:00+00:00",
            "valid_until": "2025-06-01T00:00:00+00:00",
            "supersedes_id": None,
            "evidence_ids": ["e-old"],
        }
    ],
    "evidence": [
        {
            "id": "e-new",
            "claim_id": "c-new",
            "version_id": "v-new",
            "document_id": "d-new",
            "path": "docs/tech/postgres.md",
            "source_hash": "h-new",
            "chunk_id": "k-new",
            "heading": "Datastore",
            "text": "The primary datastore is PostgreSQL.",
            "start_offset": 0,
            "end_offset": 35,
            "observed_at": "2025-06-01T00:00:00+00:00",
        },
        {
            "id": "e-old",
            "claim_id": "c-old",
            "version_id": "v-old",
            "document_id": "d-old",
            "path": "adrs/adr-011.md",
            "source_hash": "h-old",
            "chunk_id": "k-old",
            "heading": "Edge store",
            "text": "The primary datastore is SQLite.",
            "start_offset": 0,
            "end_offset": 30,
            "observed_at": "2025-01-01T00:00:00+00:00",
        },
    ],
    "sources": [
        {
            "document_id": "d-new",
            "version_id": "v-new",
            "path": "docs/tech/postgres.md",
            "source_hash": "h-new",
            "observed_at": "2025-06-01T00:00:00+00:00",
        },
        {
            "document_id": "d-old",
            "version_id": "v-old",
            "path": "adrs/adr-011.md",
            "source_hash": "h-old",
            "observed_at": "2025-01-01T00:00:00+00:00",
        },
    ],
}


@pytest.fixture
def workspace(tmp_path) -> Path:
    pack = MemoryPack.from_dict(PACK_FIXTURE)
    (tmp_path / "pack.json").write_text(pack.canonical_json(), encoding="utf-8")
    proof = build_proof(pack, "architecture.postgres")
    (tmp_path / "proof.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8"
    )
    return tmp_path


def _run(argv, cwd):
    """Invoke the CLI as a subprocess, the way a developer would."""
    return subprocess.run(
        [sys.executable, str(ROOT / "memory_proof_cli.py"), *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# --- the product workflow ----------------------------------------------------


def test_prove_then_verify_round_trip(workspace):
    """prove -> verify -> VERIFIED, through the CLI."""
    result = _run(
        ["prove", "--pack", "pack.json", "--claim-key", "architecture.postgres", "-o", "new.json"],
        workspace,
    )
    assert result.returncode == EXIT_OK, result.stderr

    verified = _run(["verify", "new.json", "--pack", "pack.json"], workspace)
    assert verified.returncode == EXIT_OK, verified.stderr
    assert "VERIFIED" in verified.stdout
    assert "architecture.postgres" in verified.stdout


def test_explain_shows_provenance_not_reasoning(workspace):
    result = _run(["explain", "proof.json", "--pack", "pack.json"], workspace)
    assert result.returncode == EXIT_OK, result.stderr
    for section in ("ANSWER", "WHY THIS MEMORY", "TIME", "EVIDENCE", "HISTORY", "RECEIPT"):
        assert section in result.stdout, f"missing {section}"
    assert "The primary datastore is PostgreSQL." in result.stdout
    # It states its own limits rather than implying external truth.
    assert "does not establish that the original source was factually correct" in result.stdout
    assert "does not establish authenticity" in result.stdout


def test_verify_json_output_is_machine_readable(workspace):
    result = _run(["verify", "proof.json", "--pack", "pack.json", "--json"], workspace)
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    # The three properties are reported separately, never collapsed.
    for key in ("integrity", "provenance", "temporal", "supersession"):
        assert payload[key] is True, key
    assert payload["authenticity"]["authenticated"] is False
    assert "not established" in payload["authenticity"]["reason"]


# --- tamper, at CLI level ----------------------------------------------------


@pytest.mark.parametrize(
    "mutate,expected",
    [
        ("claim", "authoritative digest mismatch"),
        ("evidence", "evidence record"),
        ("timestamp", "authoritative digest mismatch"),
        ("document", "but the artifact records"),
        ("proof", "proof digest mismatch"),
    ],
)
def test_cli_rejects_every_tamper_class(workspace, mutate, expected):
    """Each tamper class must exit non-zero AND explain itself."""
    if mutate == "proof":
        path = workspace / "proof.json"
        data = json.loads(path.read_text())
        data["entries"][0]["claim"]["text"] = "asserted but not in the artifact"
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
        result = _run(["verify", "proof.json", "--pack", "pack.json"], workspace)
    else:
        path = workspace / "tampered.json"
        raw = json.loads((workspace / "pack.json").read_text())
        if mutate == "claim":
            raw["current_memories"][0]["claim"] = "The primary datastore is MySQL."
        elif mutate == "evidence":
            raw["evidence"][0]["text"] = "Fabricated supporting quote."
        elif mutate == "timestamp":
            raw["current_memories"][0]["observed_at"] = "2099-01-01T00:00:00+00:00"
        elif mutate == "document":
            raw["sources"][0]["path"] = "somewhere/else.md"
        path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        result = _run(["verify", "proof.json", "--pack", "tampered.json"], workspace)

    assert result.returncode == EXIT_REJECTED
    assert "REJECTED" in result.stdout
    assert expected in result.stdout


def test_cli_reports_a_wrong_pack_clearly(workspace):
    other = MemoryPack.from_dict({**PACK_FIXTURE, "corpus": "somewhere-else"})
    (workspace / "other.json").write_text(other.canonical_json())
    result = _run(["verify", "proof.json", "--pack", "other.json"], workspace)
    assert result.returncode == EXIT_REJECTED
    assert "authoritative digest mismatch" in result.stdout


def test_cli_rejects_a_supersession_mismatch(workspace):
    raw = json.loads((workspace / "pack.json").read_text())
    raw["current_memories"][0]["supersedes_id"] = None
    (workspace / "chain.json").write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    result = _run(["verify", "proof.json", "--pack", "chain.json"], workspace)
    assert result.returncode == EXIT_REJECTED


# --- error semantics ---------------------------------------------------------


def test_missing_file_is_bad_input_not_a_rejection(workspace):
    result = _run(["verify", "nope.json", "--pack", "pack.json"], workspace)
    assert result.returncode == EXIT_BAD_INPUT
    assert "cannot read" in result.stderr


def test_malformed_json_is_bad_input(workspace):
    (workspace / "broken.json").write_text("{not json", encoding="utf-8")
    result = _run(["verify", "broken.json", "--pack", "pack.json"], workspace)
    assert result.returncode == EXIT_BAD_INPUT


def test_prove_refuses_an_unknown_claim_key(workspace):
    result = _run(
        ["prove", "--pack", "pack.json", "--claim-key", "nope", "-o", "x.json"], workspace
    )
    assert result.returncode == EXIT_BAD_INPUT
    assert "no claim for key" in result.stderr


def test_cli_exposes_help_for_each_command():
    for argv in (
        ["--help"],
        ["verify", "--help"],
        ["prove", "--help"],
        ["explain", "--help"],
        ["receipt", "--help"],
    ):
        result = _run(argv, ROOT)
        assert result.returncode == EXIT_OK, argv
        assert "usage:" in result.stdout


# --- the product workflow ----------------------------------------------------


def test_receipt_round_trip_through_the_cli(workspace):
    """The full loop: receipt -> export -> verify -> tamper -> restore."""
    minted = _run(
        [
            "receipt",
            "--pack",
            "pack.json",
            "--claim-key",
            "architecture.postgres",
            "--query",
            "What datastore did production use?",
            "-o",
            "receipt.json",
        ],
        workspace,
    )
    assert minted.returncode == EXIT_OK, minted.stderr
    assert "receipt id:" in minted.stdout

    verified = _run(["verify", "receipt.json", "--pack", "pack.json"], workspace)
    assert verified.returncode == EXIT_OK, verified.stderr
    assert "VERIFIED" in verified.stdout
    # The trust boundary is stated, not implied.
    assert "authenticity: NOT ESTABLISHED" in verified.stdout

    tampered = workspace / "tampered.json"
    raw = json.loads((workspace / "pack.json").read_text())
    raw["current_memories"][0]["value"] = "MySQL"
    tampered.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    rejected = _run(["verify", "receipt.json", "--pack", "tampered.json"], workspace)
    assert rejected.returncode == EXIT_REJECTED
    assert _run(["verify", "receipt.json", "--pack", "pack.json"], workspace).returncode == EXIT_OK


def test_cli_trust_anchor_changes_the_authenticity_verdict(workspace):
    """With a pinned digest authenticity becomes checkable; without one it does not."""
    pack = MemoryPack.from_dict(PACK_FIXTURE)
    _run(
        [
            "receipt",
            "--pack",
            "pack.json",
            "--claim-key",
            "architecture.postgres",
            "-o",
            "receipt.json",
        ],
        workspace,
    )

    pinned = _run(
        ["verify", "receipt.json", "--pack", "pack.json", "--trusted-digest", pack.digest()],
        workspace,
    )
    assert pinned.returncode == EXIT_OK
    assert "authenticity: VERIFIED (trust anchor matched)" in pinned.stdout

    wrong = _run(
        ["verify", "receipt.json", "--pack", "pack.json", "--trusted-digest", "b" * 64], workspace
    )
    assert wrong.returncode == EXIT_OK
    assert "authenticity: REJECTED" in wrong.stdout


def test_cli_historical_receipt_names_the_old_truth(workspace):
    """At an earlier instant the archive recorded SQLite -- a real supersession chain."""
    _run(
        [
            "receipt",
            "--pack",
            "pack.json",
            "--claim-key",
            "architecture.postgres",
            "--query",
            "What datastore was production using?",
            "--valid-at",
            "2025-02-01T00:00:00+00:00",
            "-o",
            "historical.json",
        ],
        workspace,
    )
    verified = _run(["verify", "historical.json", "--pack", "pack.json"], workspace)
    assert verified.returncode == EXIT_OK, verified.stderr
    assert "VERIFIED" in verified.stdout

    explained = _run(["explain", "historical.json", "--pack", "pack.json"], workspace)
    assert explained.returncode == EXIT_OK
    assert "The primary datastore is SQLite." in explained.stdout
    assert "2025-02-01T00:00:00+00:00" in explained.stdout


def test_cli_receipt_without_proof_still_verifies(workspace):
    _run(
        [
            "receipt",
            "--pack",
            "pack.json",
            "--claim-key",
            "architecture.postgres",
            "--no-proof",
            "-o",
            "minimal.json",
        ],
        workspace,
    )
    minimal = json.loads((workspace / "minimal.json").read_text())
    assert "proof" not in minimal
    assert _run(["verify", "minimal.json", "--pack", "pack.json"], workspace).returncode == EXIT_OK


def test_cli_explain_reports_both_honest_limits(workspace):
    _run(
        [
            "receipt",
            "--pack",
            "pack.json",
            "--claim-key",
            "architecture.postgres",
            "-o",
            "receipt.json",
        ],
        workspace,
    )
    result = _run(["explain", "receipt.json", "--pack", "pack.json"], workspace)
    assert result.returncode == EXIT_OK
    assert "does not establish that the original source was factually correct" in result.stdout
    assert "does not establish authenticity" in result.stdout


# --- packaging boundary ------------------------------------------------------


def test_standalone_modules_are_declared_for_shipping():
    """The exact failure mode: works in the source tree, absent from the wheel."""
    text = (ROOT / "pyproject.toml").read_text()
    for module in STANDALONE_MODULES + ("memory_receipt.py",):
        stem = module.removesuffix(".py")
        assert f'"{stem}"' in text, f"{module} is not declared in py-modules"
    assert 'mindpalace-proof = "memory_proof_cli:main"' in text


def test_cli_and_proof_import_nothing_heavy():
    """Import isolation: the receipt path must not pull in the runtime."""
    code = (
        "import sys;"
        "import memory_pack, memory_proof, memory_proof_cli, memory_receipt;"
        "heavy=[m for m in ('fastapi','sqlalchemy','asyncpg','torch',"
        "'sentence_transformers','pgvector','psycopg','pydantic','alembic') "
        "if m in sys.modules];"
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"heavy modules imported: {result.stdout}"


@pytest.mark.skipif(not list((ROOT / "dist").glob("*.whl")), reason="no built wheel in dist/")
def test_built_wheel_contains_the_standalone_modules():
    """Build artifacts are the distributable, not the source checkout."""
    wheel = sorted((ROOT / "dist").glob("*.whl"))[-1]
    names = set(zipfile.ZipFile(wheel).namelist())
    for module in STANDALONE_MODULES + ("memory_receipt.py",):
        assert module in names, f"{module} is missing from {wheel.name}"
    entry = (
        zipfile.ZipFile(wheel)
        .read([n for n in names if n.endswith("entry_points.txt")][0])
        .decode()
    )
    assert "mindpalace-proof = memory_proof_cli:main" in entry
