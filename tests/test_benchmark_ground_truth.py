"""Benchmark ground-truth validation tests.

These guard against benchmark contamination (Phase 3): every label must
reference a real corpus document, negative queries must have no labels,
filters must reference valid metadata values, and query IDs must be unique.

The benchmark must fail loudly if its ground truth is malformed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from api.services.evaluation import EvaluationService

CORPUS_DIR = Path("content_eval")
BENCHMARK_FILE = "eval/retrieval_benchmarks.yaml"
VALID_TYPES = {"kaggle", "project", "note", "paper"}


def _corpus_titles() -> dict[str, Path]:
    """Map frontmatter title -> source file for every corpus document."""
    titles: dict[str, Path] = {}
    for f in sorted(CORPUS_DIR.glob("*.md")):
        m = re.search(r'^title:\s*"(.+)"', f.read_text(encoding="utf-8"), re.M)
        assert m, f"missing frontmatter title in {f}"
        title = m.group(1)
        assert title not in titles, f"duplicate corpus title: {title}"
        titles[title] = f
    return titles


@pytest.fixture(scope="module")
def benchmarks() -> list[dict]:
    return EvaluationService.load_benchmarks(BENCHMARK_FILE)


def test_every_expected_title_exists_in_corpus(benchmarks):
    actual = set(_corpus_titles())
    bad = [(e["query"], d) for e in benchmarks for d in e["expected"] if d not in actual]
    assert not bad, f"labels referencing non-existent documents: {bad}"


def test_negative_queries_have_no_labels(benchmarks):
    bad = [e["query"] for e in benchmarks if e.get("expect_empty") and e["expected"]]
    assert not bad, f"negative queries with expected docs: {bad}"


def test_all_queries_have_categories(benchmarks):
    missing = [e["query"] for e in benchmarks if not e.get("category")]
    assert not missing, f"queries without categories: {missing}"


def test_filters_reference_valid_metadata(benchmarks):

    corpus_tags: set[str] = set()
    for f in CORPUS_DIR.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^tags:\s*\[(.+)\]", text, re.M)
        if m:
            corpus_tags |= {t.strip().strip("\"'") for t in m.group(1).split(",")}

    for e in benchmarks:
        for f in e.get("filters", {}).values():
            if isinstance(f, dict):
                raise AssertionError(f"nested filter values unsupported: {e['query']}")
        dt = e.get("filters", {}).get("document_type")
        if dt is not None:
            assert dt in VALID_TYPES, f"invalid document_type filter '{dt}' in: {e['query']}"
        tags = e.get("filters", {}).get("tags")
        if tags:
            unknown = set(tags) - corpus_tags
            assert not unknown, f"unknown tag filter {unknown} in: {e['query']}"


def test_benchmark_loads_without_duplicates(benchmarks):
    # load_benchmarks raises on duplicates; reaching here means it passed.
    assert len(benchmarks) > 0


def test_benchmark_scale_appropriate_for_corpus():
    """A 200+-document corpus needs a proportionally larger benchmark."""
    n_docs = len(_corpus_titles())
    data = yaml.safe_load(open(BENCHMARK_FILE))
    if n_docs >= 150:
        assert (
            len(data) >= 54
        ), f"corpus grew to {n_docs} docs but benchmark has only {len(data)} queries"
