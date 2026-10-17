"""The query log records what was asked, grades what came back, and never fails a query.

Synthetic benchmarks measure the questions somebody thought of. They cannot
measure the ones a developer's agent actually asks, and they drift the moment the
corpus changes underneath them. This module records real traffic instead.

Two properties decide whether that is worth having:

* it never puts memory content on disk. The log sits in the same home directory as
  the database it describes, which is exactly where content ends up by accident.
* writing a row never fails the query. A full disk costs some telemetry and
  nothing else.
"""

import json

import pytest

from api.services import query_log


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "queries.jsonl"
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", str(path))
    return path


def rows(path):
    """Every parseable line. A corrupt line is skipped rather than fatal here."""
    parsed = []
    for line in path.read_text().splitlines():
        try:
            parsed.append(json.loads(line))
        except ValueError:
            continue
    return parsed


def test_a_recall_row_carries_the_ranking_and_the_latency(log):
    identifier = query_log.record(
        operation="query",
        query="What is the primary datastore?",
        started=0.0,
        corpus="project",
        match_status="ambiguous",
        match_margin=0.04,
        top_scores=[0.71, 0.67, 0.22, 0.19],
        returned_claim_ids=["a" * 64, "b" * 64],
    )
    assert identifier
    row = rows(log)[0]
    assert row["operation"] == "query"
    assert row["query"] == "What is the primary datastore?"
    assert row["match_status"] == "ambiguous"
    assert row["match_margin"] == pytest.approx(0.04)
    assert row["returned_claim_ids"] == ["a" * 64, "b" * 64]
    assert row["latency_ms"] >= 0
    assert row["timestamp"].endswith("+00:00")


def test_only_the_top_three_scores_are_kept(log):
    """A fourth number nobody reads is not more information."""
    query_log.record(
        operation="query", query="q", started=0.0, top_scores=[0.9, 0.8, 0.7, 0.6, 0.5]
    )
    assert rows(log)[0]["top_scores"] == [0.9, 0.8, 0.7]


def test_no_memory_content_is_written(log):
    """A log that survives on a laptop is a place memory content ends up."""
    query_log.record(
        operation="query",
        query="What is the primary datastore?",
        started=0.0,
        match_status="confident",
        top_scores=[0.9],
        returned_claim_ids=["a" * 64],
    )
    blob = log.read_text()
    assert "PostgreSQL" not in blob
    assert "adr-001" not in blob
    # The corpus is not recorded either: a log that names corpora is a map.
    assert "project" not in blob


def test_the_query_text_is_bounded(log):
    query_log.record(operation="query", query="x" * 5000, started=0.0)
    assert len(rows(log)[0]["query"]) <= query_log.QUERY_LIMIT


def test_a_retry_is_two_rows_not_one_quiet_merge(log):
    """Two rows with the same question are a fact about the traffic."""
    first = query_log.record(operation="query", query="same question", started=1.0)
    second = query_log.record(operation="query", query="same question", started=2.0)
    assert first != second
    assert len(rows(log)) == 2


def test_an_unwritable_path_returns_none_and_does_not_raise(monkeypatch, tmp_path):
    """A full disk must not turn a recall into a 500.

    Two shapes: a directory where a file is expected, and a path Python rejects
    outright. `mkdir` raises `ValueError` rather than `OSError` on the second, so
    a guard that only catches the first turns an unusual path into an exception
    in the middle of somebody's query.
    """
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", str(tmp_path))  # a directory
    assert query_log.record(operation="query", query="q", started=0.0) is None

    monkeypatch.setattr(query_log, "default_path", lambda: tmp_path / "x" / "\0bad")
    assert query_log.record(operation="query", query="q", started=0.0) is None

    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(query_log, "default_path", lambda: blocker / "logs" / "q.jsonl")
    assert query_log.record(operation="query", query="q", started=0.0) is None


def test_the_log_is_off_for_a_tenant_scoped_connection(monkeypatch):
    """A server deployment's retention policy is not this module's to choose."""
    monkeypatch.delenv("MIND_PALACE_QUERY_LOG", raising=False)
    monkeypatch.setenv("MIND_PALACE_TENANT_SCOPE", "some-corpus-id")
    assert query_log.tenant_scoped_now() is True
    assert query_log.enabled(tenant_scoped=True) is False


def test_the_log_is_off_unless_asked_for(monkeypatch):
    """A row carries the caller's query text, so writing one is opt-in.

    It used to be on for any loopback connection, which is every local install and
    every test session -- so `docs/security.md`'s promise that logs hold no query
    text was false on the machine making it. The setting is the whole contract now.
    """
    monkeypatch.delenv("MIND_PALACE_QUERY_LOG", raising=False)
    monkeypatch.delenv("MIND_PALACE_TENANT_SCOPE", raising=False)
    assert query_log.tenant_scoped_now() is False
    assert query_log.enabled(tenant_scoped=False) is False


def test_setting_it_to_one_turns_the_log_on_everywhere(monkeypatch):
    """Local or deployed, opt-in means opt-in. `tenant_scoped` no longer decides."""
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", "1")
    assert query_log.enabled(tenant_scoped=False) is True
    assert query_log.enabled(tenant_scoped=True) is True


def test_the_setting_overrides_both_ways(monkeypatch):
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", "0")
    assert query_log.enabled(tenant_scoped=False) is False
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", "1")
    assert query_log.enabled(tenant_scoped=True) is True


def test_feedback_appends_a_second_row_pointing_at_the_first(log):
    identifier = query_log.record(
        operation="query", query="What cache do we use?", started=0.0, match_status="confident"
    )
    stamped = query_log.feedback(identifier, "right", note="redis is right")
    assert stamped is not None
    written = rows(log)
    assert len(written) == 2
    assert written[1]["kind"] == "feedback"
    assert written[1]["verdict"] == "right"
    assert written[1]["query_id"] == identifier
    assert written[1]["original_match_status"] == "confident"


def test_feedback_against_a_row_that_does_not_exist_is_refused(log):
    """Feedback that silently lands nowhere looks exactly like nothing being wrong."""
    assert query_log.feedback("deadbeef0000", "right") is None


def test_an_unknown_verdict_is_refused(log):
    query_log.record(operation="query", query="q", started=0.0)
    with pytest.raises(ValueError, match="verdict must be one of"):
        query_log.feedback("x", "excellent")


def test_a_corrupt_line_does_not_break_feedback(log):
    """One bad line must not cost the whole log its graded rows."""
    query_log.record(operation="query", query="q", started=0.0)
    with log.open("a") as handle:
        handle.write("{not json\n")
    stamped = query_log.feedback(rows(log)[0]["query_id"], "wrong")
    assert stamped["verdict"] == "wrong"


def test_the_context_manager_logs_when_the_operation_raises(tmp_path, monkeypatch):
    """A failed call is traffic too, and it is the traffic worth having."""
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", "1")
    path = tmp_path / "logs" / "queries.jsonl"
    monkeypatch.setenv("MIND_PALACE_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        with query_log.Call("query", "a question that failed", corpus="c"):
            raise RuntimeError("boom")
    assert len(rows(path)) == 1
