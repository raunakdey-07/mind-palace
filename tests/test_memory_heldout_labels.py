"""Frozen heldout labels: local YAML/source checks only, never retrieval.

The digest is the split-freeze boundary. Do not update it to accommodate service
output. These checks verify authored subjects and complete version-aware labels;
semantic novelty and unrelatedness remain independent author judgments.
"""

import hashlib
import re
from collections import Counter
from itertools import combinations
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "eval/memory_questions_heldout.yaml"
BENCHMARK_PATH = ROOT / "eval/memory_benchmarks.yaml"
FROZEN_SHA256 = "77e05babeeb0dd62d08a03865728d8ffffe50c20b57066566ab865c2dc37757a"
ALIASES = "ABCDEFG"
CATEGORIES = {
    "current",
    "historical",
    "temporal",
    "multi-topic",
    "conflict-provenance",
    "abstention",
}
INTENTS = {"current", "historical", "temporal", "change", "conflict", "provenance"}
LABELS = {"expected_current_claims", "expected_historical_claims", "expected_conflicts"}
REQUIRED = {"id", "question", "category", "stage", "intent", "expected_keys"} | LABELS
OPTIONAL = {"as_of", "path", "expected_topics", "expected"}


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate fields rather than silently replacing frozen labels."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        assert key not in result, "Duplicate YAML mapping key"
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


HELDOUT = load(SPEC)
BENCHMARK = load(BENCHMARK_PATH)
QUERIES = HELDOUT["queries"]


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def strings(items, nonempty=False):
    assert isinstance(items, list)
    assert not nonempty or items
    assert all(isinstance(item, str) and item and item == item.strip() for item in items)
    assert len(items) == len(set(items))


def test_split_freeze_and_coverage():
    assert hashlib.sha256(SPEC.read_bytes()).hexdigest() == FROZEN_SHA256
    assert set(HELDOUT) == {"version", "queries"}
    assert type(HELDOUT["version"]) is int and HELDOUT["version"] == 1
    assert isinstance(QUERIES, list) and len(QUERIES) >= 60
    counts = Counter(q["category"] for q in QUERIES)
    assert set(counts) == CATEGORIES
    assert all(count >= 10 for count in counts.values())
    assert {q["stage"] for q in QUERIES} == set(ALIASES)
    assert {q["as_of"] for q in QUERIES if "as_of" in q} == set("ABC")
    ids = [q["id"] for q in QUERIES]
    questions = [normalized(q["question"]) for q in QUERIES]
    assert len(ids) == len(set(ids))
    assert len(questions) == len(set(questions))
    development = BENCHMARK["queries"] + load(ROOT / "eval/memory_questions.yaml")["queries"]
    assert not set(ids) & {q["id"] for q in development}
    assert not set(questions) & {normalized(q["question"]) for q in development}


@pytest.mark.parametrize("question", QUERIES, ids=lambda q: q["id"])
def test_query_format(question):
    assert REQUIRED <= question.keys() <= REQUIRED | OPTIONAL
    assert re.fullmatch(r"heldout-[a-z0-9]+(?:-[a-z0-9]+)*", question["id"])
    text = question["question"]
    assert isinstance(text, str) and text == text.strip() and text.endswith("?")
    assert len(text.split()) >= 5
    assert question["category"] in CATEGORIES
    assert question["stage"] in set(ALIASES)
    assert question["intent"] in INTENTS
    strings(question["expected_keys"])
    for field in LABELS - {"expected_conflicts"}:
        strings(question[field])
    pairs = question["expected_conflicts"]
    assert isinstance(pairs, list)
    for pair in pairs:
        strings(pair, nonempty=True)
        assert len(pair) == 2
    assert len(pairs) == len({frozenset(pair) for pair in pairs})
    # Identical text MAY be both current and historical after a prose-only edit.
    assert not set(question["expected_current_claims"]) & {c for p in pairs for c in p}
    if question["category"] in {"current", "historical", "temporal"}:
        assert question["intent"] == question["category"]
    if question["category"] == "conflict-provenance":
        assert question["intent"] in {"conflict", "provenance"}
    if question["intent"] == "conflict":
        assert pairs
    if question["intent"] == "temporal":
        assert question["as_of"] in set("ABC")
        assert ALIASES.index(question["as_of"]) <= ALIASES.index(question["stage"])
    else:
        assert "as_of" not in question
    if "path" in question:
        path = question["path"]
        assert isinstance(path, str) and path.endswith(".md")
        assert not PurePosixPath(path).is_absolute()
        assert ".." not in PurePosixPath(path).parts and "\\" not in path
        assert PurePosixPath(path).as_posix() == path
    if question["category"] == "abstention":
        assert question["expected"] == "no_relevant_memory"
        assert question["intent"] == "current"
        assert all(question[label] == [] for label in LABELS)
        assert question["expected_keys"] == []
        assert not {"path", "as_of", "expected_topics"} & question.keys()
    else:
        assert "expected" not in question
        assert question["expected_keys"]
        assert any(question[label] for label in LABELS)
    if question["category"] == "multi-topic":
        assert len(question["expected_topics"]) >= 2
    if "expected_topics" in question:
        topics = question["expected_topics"]
        assert isinstance(topics, list) and topics
        strings([topic["id"] for topic in topics], nonempty=True)
        for topic in topics:
            assert set(topic) == {"id", "claims"}
            assert re.fullmatch(r"[a-z]+(?:-[a-z]+)+", topic["id"])
            strings(topic["claims"], nonempty=True)
        flat = [claim for topic in topics for claim in topic["claims"]]
        strings(flat, nonempty=True)
        expected = set(question["expected_current_claims"])
        expected.update(question["expected_historical_claims"])
        expected.update(claim for pair in pairs for claim in pair)
        assert set(flat) == expected


@pytest.fixture(scope="module")
def source_archive():
    """Track byte-distinct document versions with delete-after-payload semantics.

    Version identity is essential: unchanged claim mappings in changed documents
    still leave archived assertions. No natural-language relevance is computed.
    """
    snapshots, versions, active, bodies = {}, [], {}, []
    assert [s["id"] for s in BENCHMARK["stages"]] == list(ALIASES)
    for stage in BENCHMARK["stages"]:
        alias = stage["id"]
        directory = (BENCHMARK_PATH.parent / stage["directory"]).resolve()
        assert directory.is_relative_to(ROOT / "examples/evaluation/corpus")
        documents = {}
        for source in sorted(directory.rglob("*.md")):
            assert not source.is_symlink()
            raw = source.read_text(encoding="utf-8")
            opening, frontmatter, body = raw.split("---", 2)
            assert not opening.strip()
            claims = yaml.load(frontmatter, Loader=UniqueKeyLoader)["claims"]
            assert isinstance(claims, list) and claims
            path = source.relative_to(directory).as_posix()
            bodies.append(raw.casefold())
            for claim in claims:
                assert {"key", "value", "claim", "evidence"} <= claim.keys()
                assert claim["claim"] in body
                quotes = claim["evidence"]
                quotes = [quotes] if isinstance(quotes, str) else quotes
                strings(quotes, nonempty=True)
                assert all(quote in body for quote in quotes)
            documents[path] = {"raw": raw, "claims": claims}
        for deleted in stage["delete"]:
            assert deleted in documents
            del documents[deleted]
        for path, doc in documents.items():
            if path in active and active[path]["raw"] == doc["raw"]:
                continue
            version = {
                "raw": doc["raw"],
                "claims": [dict(c, path=path, version=(alias, path)) for c in doc["claims"]],
            }
            versions.append(version)
            active[path] = version
        for deleted in active.keys() - documents.keys():
            del active[deleted]
        current = [c for v in active.values() for c in v["claims"]]
        current_versions = {c["version"] for c in current}
        historical = [
            c for v in versions for c in v["claims"] if c["version"] not in current_versions
        ]
        snapshots[alias] = {"current": current, "historical": historical}
    return snapshots, "\n".join(bodies)


def conflicts(claims):
    return [
        (left, right)
        for left, right in combinations(claims, 2)
        if left["path"] != right["path"]
        and left["key"] == right["key"]
        and left["value"] != right["value"]
    ]


@pytest.mark.parametrize("question", QUERIES, ids=lambda q: q["id"])
def test_exact_complete_source_labels(question, source_archive):
    snapshots, _ = source_archive
    state = snapshots[question.get("as_of", question["stage"])]
    archive = state["current"] + state["historical"]
    keys = set(question["expected_keys"])
    assert keys <= {c["key"] for c in archive}
    if "path" in question:
        assert any(c["path"] == question["path"] for c in archive)

    def selected(claim):
        return claim["key"] in keys and (
            "path" not in question or claim["path"] == question["path"]
        )

    live_pairs = conflicts(state["current"])
    conflicting = {c["version"] for pair in live_pairs for c in pair}
    current = {
        c["claim"] for c in state["current"] if selected(c) and c["version"] not in conflicting
    }
    history = set()
    if question["intent"] in {"historical", "change", "provenance"}:
        history = {c["claim"] for c in state["historical"] if selected(c)}
    expected_pairs = {
        frozenset(c["claim"] for c in pair) for pair in live_pairs if any(selected(c) for c in pair)
    }
    assert set(question["expected_current_claims"]) == current
    assert set(question["expected_historical_claims"]) == history
    assert {frozenset(pair) for pair in question["expected_conflicts"]} == expected_pairs
    labeled = current | history | {c for pair in expected_pairs for c in pair}
    assert {c["key"] for c in archive if c["claim"] in labeled} == keys
    if "expected_topics" in question:
        topic_keys = []
        for topic in question["expected_topics"]:
            subjects = {c["key"] for c in archive if c["claim"] in topic["claims"]}
            assert len(subjects) == 1
            topic_keys.extend(subjects)
        assert len(topic_keys) == len(set(topic_keys))
        assert set(topic_keys) == keys


def test_prose_revision_and_lifecycle_guards(source_archive):
    snapshots, _ = source_archive
    owner = "The Platform team owns Dispatch replay recovery."
    assert owner in {c["claim"] for c in snapshots["B"]["current"]}
    assert owner in {c["claim"] for c in snapshots["B"]["historical"]}
    assert any(
        owner in q["expected_current_claims"] and owner in q["expected_historical_claims"]
        for q in QUERIES
        if q["category"] == "historical"
    )
    stale = "runbooks/auth-incident.md"
    assert stale not in {c["path"] for c in snapshots["F"]["current"]}
    assert stale in {c["path"] for c in snapshots["F"]["historical"]}
    assert len(conflicts(snapshots["E"]["current"])) == 1
    assert conflicts(snapshots["F"]["current"]) == []
    assert len(conflicts(snapshots["G"]["current"])) == 1
    assert {q["stage"] for q in QUERIES if q["expected_conflicts"]} == {"E", "G"}
    assert (
        sum(q["category"] == "multi-topic" and bool(q["expected_conflicts"]) for q in QUERIES) >= 2
    )


def test_manual_abstention_subjects_absent_from_all_source_bytes(source_archive):
    # These anchors guard the author's whole-corpus absence review against future
    # fixture additions. They are not a relevance algorithm or proof of semantics.
    subjects = {
        "lunar-calendar": ("lunar eclipse", "kyoto"),
        "sourdough-ratio": ("sourdough", "rye"),
        "museum-hours": ("prado", "museum"),
        "orchid-care": ("orchid",),
        "violin-tuning": ("violin",),
        "marathon-record": ("marathon",),
        "mongolian-currency": ("mongolia", "currency"),
        "pottery-firing": ("stoneware", "pottery", "kiln"),
        "novel-translator": ("genji",),
        "chess-castling": ("chess", "castling"),
    }
    _, raw = source_archive
    negatives = {q["id"]: q for q in QUERIES if q["category"] == "abstention"}
    assert set(negatives) == {f"heldout-abstain-{suffix}" for suffix in subjects}
    for suffix, anchors in subjects.items():
        text = negatives[f"heldout-abstain-{suffix}"]["question"].casefold()
        for anchor in anchors:
            pattern = rf"\b{re.escape(anchor)}\b"
            assert re.search(pattern, text)
            assert not re.search(pattern, raw)
