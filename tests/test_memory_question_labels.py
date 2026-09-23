"""Validate hand-authored question labels using only local YAML and Markdown.

No production parser, benchmark runner, memory service, model, or database is
used. These checks establish structural and corpus support, not whether a
retriever can answer the natural-language questions correctly.
"""

import re
from itertools import combinations
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = ROOT / "eval/memory_benchmarks.yaml"
QUESTIONS_PATH = ROOT / "eval/memory_questions.yaml"
BENCHMARK = yaml.safe_load(BENCHMARK_PATH.read_text(encoding="utf-8"))
QUESTIONS = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
ALIASES = "ABCDEFG"
INTENTS = {"current", "historical", "temporal", "change", "conflict", "provenance"}
LABELS = {"expected_current_claims", "expected_historical_claims", "expected_conflicts"}
REQUIRED = {"id", "question", "stage", "intent"} | LABELS
STALE_PATH = "runbooks/auth-incident.md"


@pytest.fixture(scope="module")
def corpus():
    """Read authored assertions, then apply manifest deletions to each payload."""
    states = {}
    for stage in BENCHMARK["stages"]:
        directory = (BENCHMARK_PATH.parent / stage["directory"]).resolve()
        assert directory.is_relative_to(ROOT / "examples/evaluation/corpus")
        assert directory.is_dir()
        documents = {}
        for source in sorted(directory.rglob("*.md")):
            raw = source.read_text(encoding="utf-8")
            opening, frontmatter, body = raw.split("---", 2)
            assert not opening.strip(), source
            authored = yaml.safe_load(frontmatter)["claims"]
            path = source.relative_to(directory).as_posix()
            documents[path] = []
            for claim in authored:
                text = claim["claim"]
                assert isinstance(text, str) and text in body, source
                evidence = claim["evidence"]
                quotes = [evidence] if isinstance(evidence, str) else evidence
                assert isinstance(quotes, list) and quotes, source
                assert all(isinstance(quote, str) and quote and quote in body for quote in quotes)
                documents[path].append({**claim, "path": path})
        assert documents, directory
        for deleted in stage["delete"]:
            assert deleted in documents
            del documents[deleted]
        states[stage["id"]] = documents
    assert list(states) == list(ALIASES)
    return states


def rows(documents):
    return [claim for claims in documents.values() for claim in claims]


def identity(claim):
    return claim["path"], claim["key"], claim["value"], claim["claim"]


def live_conflicts(claims):
    return [
        (left, right)
        for left, right in combinations(claims, 2)
        if left["path"] != right["path"]
        and left["key"] == right["key"]
        and left["value"] != right["value"]
    ]


def selected(question, claim):
    return "path" not in question or question["path"] == claim["path"]


def test_question_schema_and_additional_coverage():
    assert set(QUESTIONS) == {"version", "queries"}
    assert type(QUESTIONS["version"]) is int and QUESTIONS["version"] == 1
    queries = QUESTIONS["queries"]
    assert isinstance(queries, list) and len(queries) >= 30
    ids = [q["id"] for q in queries]
    text = [q["question"].strip().casefold() for q in queries]
    assert len(ids) == len(set(ids))
    assert len(text) == len(set(text))
    assert not set(ids) & {q["id"] for q in BENCHMARK["queries"]}
    assert not set(text) & {q["question"].strip().casefold() for q in BENCHMARK["queries"]}
    assert {q["stage"] for q in queries} == set(ALIASES)
    assert {q["intent"] for q in queries} == INTENTS
    assert {q["as_of"] for q in queries if q["intent"] == "temporal"} == set("ABC")
    assert {q["stage"] for q in queries if q["expected_conflicts"]} == {"E", "G"}
    assert {q["intent"] for q in queries if q["stage"] == "F"} >= {
        "current",
        "historical",
        "provenance",
    }
    assert any(q["intent"] == "current" and "Redis" in q["question"] for q in queries)
    assert any(q["intent"] == "current" and "JWT" in q["question"] for q in queries)


@pytest.mark.parametrize("question", QUESTIONS["queries"], ids=lambda q: q["id"])
def test_question_structure(question):
    assert REQUIRED <= question.keys()
    assert question.keys() <= REQUIRED | {"as_of", "path"}
    assert isinstance(question["id"], str)
    assert re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", question["id"])
    assert isinstance(question["question"], str)
    assert question["question"] == question["question"].strip()
    assert len(question["question"].split()) >= 5
    assert question["question"].endswith("?")
    assert question["stage"] in set(ALIASES)
    assert question["intent"] in INTENTS
    if question["intent"] == "temporal":
        assert question["as_of"] in set("ABC")
        assert ALIASES.index(question["as_of"]) <= ALIASES.index(question["stage"])
    else:
        assert "as_of" not in question
    if "path" in question:
        path = question["path"]
        assert isinstance(path, str) and path
        assert not PurePosixPath(path).is_absolute()
        assert ".." not in PurePosixPath(path).parts
        assert "\\" not in path
        assert PurePosixPath(path).as_posix() == path
        assert path.endswith(".md")
    for label in LABELS:
        assert isinstance(question[label], list)
    for label in ("expected_current_claims", "expected_historical_claims"):
        assert all(isinstance(c, str) and c.strip() == c and c for c in question[label])
        assert len(question[label]) == len(set(question[label]))
    pairs = question["expected_conflicts"]
    for pair in pairs:
        assert isinstance(pair, list) and len(pair) == 2
        assert all(isinstance(c, str) and c.strip() == c and c for c in pair)
        assert pair[0] != pair[1]
    assert len(pairs) == len({frozenset(pair) for pair in pairs})
    current = set(question["expected_current_claims"])
    historical = set(question["expected_historical_claims"])
    conflicting = {c for pair in pairs for c in pair}
    assert not current & historical
    assert not (current | historical) & conflicting
    if question["intent"] == "conflict":
        assert pairs


@pytest.mark.parametrize("question", QUESTIONS["queries"], ids=lambda q: q["id"])
def test_labels_have_exact_selected_corpus_support(question, corpus):
    cutoff = question.get("as_of", question["stage"])
    active = rows(corpus[cutoff])
    archive = [
        claim for alias in ALIASES[: ALIASES.index(cutoff) + 1] for claim in rows(corpus[alias])
    ]
    if "path" in question:
        # A deleted path is still a valid selector, but a future/unseen path is not.
        assert any(claim["path"] == question["path"] for claim in archive)
    pairs = live_conflicts(active)
    conflicting = {identity(claim) for pair in pairs for claim in pair}
    unopposed = {
        claim["claim"]
        for claim in active
        if identity(claim) not in conflicting and selected(question, claim)
    }
    active_ids = {identity(claim) for claim in active}
    historical = {
        claim["claim"]
        for claim in archive
        if identity(claim) not in active_ids and selected(question, claim)
    }
    # Only exact frontmatter claims count, not a substring, paraphrase, or a stale
    # mechanism mentioned incidentally in a newer document's body.
    assert set(question["expected_current_claims"]) <= unopposed
    assert set(question["expected_historical_claims"]) <= historical
    supported_pairs = {
        frozenset(claim["claim"] for claim in pair)
        for pair in pairs
        if any(selected(question, claim) for claim in pair)
    }
    expected_pairs = {frozenset(pair) for pair in question["expected_conflicts"]}
    assert expected_pairs <= supported_pairs
    if question["intent"] == "conflict":
        assert expected_pairs == supported_pairs
    if question["intent"] == "current":
        assert question["expected_historical_claims"] == []


def test_deletion_overrides_physical_payload_and_restoration(corpus):
    stage_f = next(stage for stage in BENCHMARK["stages"] if stage["id"] == "F")
    assert (BENCHMARK_PATH.parent / stage_f["directory"] / STALE_PATH).is_file()
    assert STALE_PATH not in corpus["F"]
    assert corpus["E"][STALE_PATH] == corpus["G"][STALE_PATH]
    assert len(live_conflicts(rows(corpus["E"]))) == 1
    assert live_conflicts(rows(corpus["F"])) == []
    assert len(live_conflicts(rows(corpus["G"]))) == 1
    deleted = [q for q in QUESTIONS["queries"] if q["stage"] == "F" and q.get("path") == STALE_PATH]
    assert {q["intent"] for q in deleted} == {"current", "historical", "provenance"}
    for question in deleted:
        assert question["expected_current_claims"] == []
        assert question["expected_conflicts"] == []
        if question["intent"] in {"historical", "provenance"}:
            assert question["expected_historical_claims"] == [
                "The Dispatch incident runbook prescribes JWT bearer tokens for operator requests."
            ]


def test_distinct_system_roles_and_irrelevant_negatives():
    by_id = {q["id"]: q for q in QUESTIONS["queries"]}
    roles = {
        "question-ledger-not-stream": "Dispatch stores authoritative order records in PostgreSQL.",
        "question-schema-rollout-not-storage": (
            "Dispatch migrates PostgreSQL schemas with expand-contract changes."
        ),
        "question-transport-not-ledger": "Dispatch uses Kafka for event streaming.",
    }
    for question_id, claim in roles.items():
        question = by_id[question_id]
        assert question["expected_current_claims"] == [claim]
        assert question["expected_historical_claims"] == []
        assert question["expected_conflicts"] == []
    combined = by_id["question-three-system-roles"]
    assert set(combined["expected_current_claims"]) == set(roles.values())
    # These are manual negative judgments, not relevance inferred by a service.
    for question_id in ("question-unrelated-weather", "question-unrelated-payroll"):
        question = by_id[question_id]
        assert "path" not in question
        assert all(question[label] == [] for label in LABELS)
