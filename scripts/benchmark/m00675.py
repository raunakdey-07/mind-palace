"""Canonicalize and fingerprint an M006.75 benchmark result."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN = {
    "dataset": ROOT / "eval/memory_questions_heldout.yaml",
    "policy": ROOT / "eval/memory_query_policy.json",
}
SOURCES = (
    ROOT / "api/services/memory_relevance.py",
    ROOT / "api/services/memory_query.py",
    ROOT / "api/services/memory_public.py",
    ROOT / "api/services/memory_query_benchmark.py",
    ROOT / "api/services/memory_benchmark.py",
    ROOT / "api/models/memory.py",
)
# The held-out artifact is frozen at the M006.75 schema boundary. Later
# post-release migrations are deliberately excluded so infrastructure changes do
# not rewrite the released research result or its fixture identity.
FROZEN_MIGRATIONS = (
    ROOT / "migrations/versions/001_initial_schema.py",
    ROOT / "migrations/versions/002_corpora.py",
    ROOT / "migrations/versions/003_manifest_corpus.py",
    ROOT / "migrations/versions/004_memory.py",
    ROOT / "migrations/versions/005_multiple_evidence.py",
)
FIXTURE_FILES = (
    ROOT / "eval/memory_benchmarks.yaml",
    *sorted((ROOT / "examples/evaluation/corpus").rglob("*.md")),
    *FROZEN_MIGRATIONS,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value, key=None):
    if isinstance(value, dict):
        return {
            name: canonical(item, name)
            for name, item in sorted(value.items())
            if name not in {"timings", "frozen_timing", "timestamp"}
        }
    if isinstance(value, list):
        return [canonical(item, key) for item in value]
    return value


def source_manifest():
    return {str(path.relative_to(ROOT)): sha256(path) for path in SOURCES}


def source_fingerprint():
    return hashlib.sha256(
        json.dumps(source_manifest(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def fixture_hash() -> str:
    digest = hashlib.sha256()
    for path in FIXTURE_FILES:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def model_fingerprint(model_name: str) -> dict:
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    cache_name = model_name.replace("/", "--")
    if "/" not in model_name:
        cache_name = "sentence-transformers--" + cache_name
    model_dir = cache_root / ("models--" + cache_name)
    ref = model_dir / "refs" / "main"
    if not ref.is_file():
        raise ValueError(f"Embedding model cache is missing: {model_name}")
    revision = ref.read_text(encoding="utf-8").strip()
    snapshot = model_dir / "snapshots" / revision
    if not snapshot.is_dir():
        raise ValueError(f"Embedding model snapshot is missing: {revision}")
    files = sorted(path for path in snapshot.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(snapshot)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "identifier": model_name,
        "revision": revision,
        "snapshot_sha256": digest.hexdigest(),
        "dimension": 384,
        "normalized": True,
        "metric": "cosine",
    }


def dependency_manifest():
    names = (
        "asyncpg",
        "numpy",
        "psycopg2-binary",
        "pytest",
        "sentence-transformers",
        "sqlalchemy",
        "torch",
        "transformers",
        "pyyaml",
    )
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f"Required dependency is missing: {name}") from None
    return result


def git_revision():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def build_artifact(result):
    if result.get("mode") != "heldout":
        raise ValueError("M006.75 canonical artifact requires mode=heldout")
    if result.get("question_sha256") != sha256(FROZEN["dataset"]):
        raise ValueError("Result dataset hash does not match the frozen held-out file")
    policy = json.loads(FROZEN["policy"].read_text(encoding="utf-8"))
    if result.get("freeze", {}).get("file_sha256") != sha256(FROZEN["policy"]):
        raise ValueError("Result policy hash does not match the frozen policy file")
    policy_name = "frozen"
    rows = [row for row in result.get("cases", []) if row.get("policy") == policy_name]
    if len(rows) != 60:
        raise ValueError("Held-out canonical artifact requires 60 frozen-policy rows")
    categories = {}
    for category in sorted({row.get("category") for row in rows}):
        members = [row for row in rows if row.get("category") == category]
        categories[category] = {
            "n": len(members),
            "exact": sum(row.get("exact", False) for row in members),
            "fully_correct": sum(row.get("fully_correct", False) for row in members),
            "abstention": sum(row.get("abstention", {}).get("passed", False) for row in members),
        }
    model_name = result.get("model")
    if model_name != "all-MiniLM-L6-v2":
        raise ValueError("Unexpected embedding model")
    artifact = {
        "benchmark_id": "mind-palace-m006.75",
        "benchmark_version": "1.0",
        "source_revision": git_revision(),
        "source_fingerprint": source_fingerprint(),
        "dataset_sha256": sha256(FROZEN["dataset"]),
        "policy_sha256": sha256(FROZEN["policy"]),
        "policy_parameters": policy["policy"],
        "dependency_manifest": source_manifest(),
        "database_fixture_sha256": fixture_hash(),
        "python": platform.python_version(),
        "model": model_fingerprint(model_name),
        "dependencies": dependency_manifest(),
        "embeddings": result.get("embeddings"),
        "mode": result["mode"],
        "policy": policy_name,
        "summary": canonical(result["summary"][policy_name]),
        "categories": categories,
        "safety_failures": canonical(result.get("safety_failures", [])),
        "execution_failures": canonical(result.get("execution_failures", [])),
        "questions": [
            {
                "id": row["id"],
                "policy": row.get("policy"),
                "category": row.get("category"),
                "exact": row.get("exact"),
                "fully_correct": row.get("fully_correct"),
                "abstention": canonical(row.get("abstention")),
                "subject": canonical(row.get("subject")),
                "topics": canonical(row.get("topics")),
                "safety": canonical(row.get("safety")),
            }
            for row in rows
        ],
    }
    encoded = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    artifact["result_sha256"] = hashlib.sha256(encoded).hexdigest()
    return artifact


def verify_artifact(path: Path):
    artifact = json.loads(path.read_text(encoding="utf-8"))
    expected = artifact.pop("result_sha256", None)
    encoded = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    actual = hashlib.sha256(encoded).hexdigest()
    if expected != actual:
        raise ValueError("BENCHMARK INTEGRITY FAILURE: result hash mismatch")
    if artifact["dataset_sha256"] != sha256(FROZEN["dataset"]):
        raise ValueError("BENCHMARK INTEGRITY FAILURE: dataset hash mismatch")
    if artifact["policy_sha256"] != sha256(FROZEN["policy"]):
        raise ValueError("BENCHMARK INTEGRITY FAILURE: policy hash mismatch")
    if artifact["source_fingerprint"] != source_fingerprint():
        raise ValueError("BENCHMARK INTEGRITY FAILURE: source fingerprint mismatch")
    if artifact["database_fixture_sha256"] != fixture_hash():
        raise ValueError("BENCHMARK INTEGRITY FAILURE: fixture hash mismatch")
    if artifact["model"] != model_fingerprint(artifact["model"]["identifier"]):
        raise ValueError("BENCHMARK INTEGRITY FAILURE: model artifact mismatch")
    current_deps = dependency_manifest()
    if artifact["dependencies"] != current_deps:
        raise ValueError("BENCHMARK INTEGRITY FAILURE: dependency version mismatch")
    print(expected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--verify")
    args = parser.parse_args()
    if args.verify:
        verify_artifact(Path(args.verify))
        return
    if not args.input or not args.output:
        parser.error("--input and --output are required unless --verify is used")
    result = json.loads(Path(args.input).read_text(encoding="utf-8"))
    artifact = build_artifact(result)
    Path(args.output).write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(artifact["result_sha256"])


if __name__ == "__main__":
    main()
