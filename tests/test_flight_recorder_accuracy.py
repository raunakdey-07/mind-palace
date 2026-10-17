"""The flight recorder describes the call that happened, and pytest does not use it.

The log exists to grade two weeks of actual usage: `mindpalace feedback <query_id>`
appends a verdict, and over a fortnight that is a graded sample of what a real
agent asked. Two things made that file unusable, and both were invisible until the
product was run on its own documentation.

**A `history` read was logged as a `query`.** `execute()` hardcoded the operation
string, and it read `returned_claim_ids` off `candidates` -- the ranking signal,
which only the ranked `query` path produces. `history` returns a timeline and has
no ranking, so a successful history read landed in the file as
`match_status: "abstained"` with no claim ids, beside a claim, a `NEW` event and
thirteen evidence items. Grading is by `query_id`, so a verdict on a history
question was filed against a row that reads as a failed recall.

**The test suite wrote into the real log.** The recorder is on by default for a
loopback connection, which is what the test database is, so every product call the
suite made appended a row to `~/.mind-palace/logs/queries.jsonl`. Thirty-two
thousand rows of invented product facts -- battery runtimes, mesh bands, campaign
targets -- in the one file meant to hold real traffic. The isolation is an
autouse session fixture in `conftest.py`; `test_the_suite_does_not_write_to_the_user_log`
is what holds it in place.
"""

import json
import os
from pathlib import Path

import pytest

from api.services import query_log


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "queries.jsonl"
    monkeypatch.setenv("MIND_PALACE_QUERY_LOG", str(path))
    return path


def rows(path):
    parsed = []
    for line in path.read_text().splitlines():
        try:
            parsed.append(json.loads(line))
        except ValueError:
            continue
    return parsed


def _change(event="NEW", relationship="LIFECYCLE"):
    """A real `Change`, built through the model so a new required field shows up."""
    from api.models.memory import Change

    return Change(
        version_id="v1",
        predecessor_id=None,
        event=event,
        path="README.md",
        observed_at="2026-10-17T00:00:00+00:00",
        memory_changed=True,
        relationship=relationship,
        previous=[],
        current=[_claim("a" * 64)],
    )


def _response(**overrides):
    """A `MemoryResponse` shaped the way the product actually builds them.

    Built through the real model rather than a stub, so a field added to the
    response later shows up here as a missing argument instead of a silent
    `.get` that hides it.
    """
    from api.models.memory import MemoryResponse

    base = {
        "query": "How large is the default install?",
        "corpus": "mind-palace-docs",
        "current_memories": [],
        "historical_memories": [],
        "uncertain_memories": [],
        "changes": [],
        "candidates": [],
        "match_status": "abstained",
        "match_margin": None,
    }
    base.update(overrides)
    return MemoryResponse(**base)


def _claim(identifier, text="a fact"):
    from api.models.memory import Claim

    return Claim(
        id=identifier,
        key=f"md:{text}",
        value=text,
        claim=text,
        status="CURRENT",
        version_id="v1",
        path="README.md",
        observed_at="2026-10-17T00:00:00+00:00",
        evidence_ids=[],
    )


def _candidate(key, score, claim_id):
    from api.models.memory import Candidate

    return Candidate(
        key=key,
        score=score,
        status="CURRENT",
        value="v",
        claim_ids=[claim_id],
        version_ids=["v1"],
        path="README.md",
        quotes=["a fact"],
    )


# --------------------------------------------------------------------------
# The operation is the one the caller asked for
# --------------------------------------------------------------------------


def test_a_ranked_read_logs_its_own_outcome():
    """The case that already worked, so the change cannot have broken it."""
    from api.services.memory_public import _logged_outcome

    response = _response(
        current_memories=[_claim("a" * 64)],
        candidates=[_candidate("md:a", 0.81, "a" * 64)],
        match_status="confident",
        match_margin=0.19,
    )
    logged = _logged_outcome(response)
    assert logged["match_status"] == "confident"
    assert logged["match_margin"] == 0.19
    assert logged["top_scores"] == [0.81]
    assert logged["returned_claim_ids"] == ["a" * 64]


def test_a_history_read_logs_what_it_actually_returned():
    """The regression: no ranking, so nothing was read off `candidates`.

    `match_status` stays at the response default of `abstained` because
    `project()` never sets it for `history` -- there is no ranking to report. The
    row has to say what came back anyway, or grading a history question files the
    verdict against a failed recall.
    """
    from api.services.memory_public import _logged_outcome

    response = _response(
        current_memories=[_claim("b" * 64)],
        changes=[_change()],
    )
    logged = _logged_outcome(response)
    assert logged["match_status"] != "abstained"
    assert logged["returned_claim_ids"] == ["b" * 64]
    assert logged["top_scores"] == [], "history produces no scores and must not invent them"
    assert logged["match_margin"] is None


def test_historical_claims_count_too():
    """A superseded claim retired today is the answer to a history question."""
    from api.services.memory_public import _logged_outcome

    response = _response(historical_memories=[_claim("c" * 64)])
    assert _logged_outcome(response)["returned_claim_ids"] == ["c" * 64]


def test_a_genuinely_empty_history_is_still_an_abstention():
    """Otherwise the fix inverts the meaning of the word."""
    from api.services.memory_public import _logged_outcome

    response = _response()
    logged = _logged_outcome(response)
    assert logged["match_status"] == "abstained"
    assert logged["returned_claim_ids"] == []


def test_a_history_that_returned_only_events_did_not_abstain():
    """`changes` is the result. A lifecycle row with no claims is still an answer."""
    from api.services.memory_public import _logged_outcome

    response = _response(changes=[_change()])
    assert _logged_outcome(response)["match_status"] != "abstained"


def test_the_operation_name_is_the_one_the_caller_passed():
    """`execute()` used to hardcode `query`, so `history` was indistinguishable."""
    import inspect

    from api.services.memory_public import execute

    source = inspect.getsource(execute)
    assert 'Call("query"' not in source
    assert "Call(operation, request.query" in source


# --------------------------------------------------------------------------
# Feedback round-trips on a history row
# --------------------------------------------------------------------------


def test_feedback_round_trips_on_a_history_row(log):
    """Item 3 of the brief, end to end and offline.

    `mindpalace feedback` reads the row by `query_id` and appends a second line
    pointing at it. A history row used to carry no claim ids and a status of
    `abstained`, so this is the exact case that could not be graded.
    """
    from api.services.memory_public import _logged_outcome

    response = _response(current_memories=[_claim("d" * 64)])
    identifier = query_log.record(
        operation="history", query=response.query, started=0.0, **_logged_outcome(response)
    )
    assert identifier is not None

    stamped = query_log.feedback(identifier, "right", note="the timeline is what I wanted")
    assert stamped is not None
    written = rows(log)
    assert len(written) == 2
    assert written[1]["kind"] == "feedback"
    assert written[1]["verdict"] == "right"
    assert written[1]["query_id"] == identifier
    assert written[1]["operation"] == "history", "the verdict must name the operation it grades"
    assert written[1]["original_match_status"] != "abstained"
    assert written[0]["returned_claim_ids"] == ["d" * 64]


def test_recall_and_history_get_distinct_rows_for_one_question(log):
    """Two weeks of grading cannot sort them apart if they share a query string."""
    from api.services.memory_public import _logged_outcome

    response = _response(
        current_memories=[_claim("e" * 64)],
        candidates=[_candidate("md:e", 0.9, "e" * 64)],
        match_status="confident",
    )
    recall_id = query_log.record(
        operation="query", query="q", started=1.0, **_logged_outcome(response)
    )
    history_id = query_log.record(
        operation="history", query="q", started=2.0, **_logged_outcome(response)
    )
    assert recall_id != history_id
    written = {r["query_id"]: r for r in rows(log)}
    assert written[recall_id]["operation"] == "query"
    assert written[history_id]["operation"] == "history"


# --------------------------------------------------------------------------
# pytest does not write into the developer's log
# --------------------------------------------------------------------------


def test_the_suite_does_not_write_to_the_user_log():
    """The autouse session fixture in `conftest.py`, asserted rather than hoped for.

    The recorder's default path is `$MIND_PALACE_DATA_DIR/logs/queries.jsonl`,
    which in this session points into pytest's `tmp_path`. If the fixture ever
    stops being autouse, or stops being session-scoped, this fails with the real
    home directory in the message rather than with 32,738 invented rows in a file
    somebody meant to grade from.
    """
    from api.services.query_log import default_path

    configured = default_path()
    home_default = Path(os.path.expanduser("~/.mind-palace")) / "logs" / "queries.jsonl"
    assert configured != home_default, (
        "the flight recorder is pointed at the real user log during pytest; "
        "tests/conftest.py:session_recorder_isolation is not in effect"
    )
    assert "pytest" in str(configured) or "flight-recorder" in str(
        configured
    ), f"expected a pytest scratch path, got {configured}"


def test_a_product_call_from_an_ordinary_test_lands_in_the_scratch_file():
    """The isolation proved by doing, not by inspecting a path.

    `execute()` is the single point every surface records through, so a call
    through it exercises the same code the real product uses. The row must appear
    under pytest's `tmp_path` and the user's real log must not have grown.
    """
    from api.services.memory_public import _logged_outcome
    from api.services.query_log import default_path

    before = _user_log_size()
    identifier = query_log.record(
        operation="history",
        query="an ordinary test asking something",
        started=0.0,
        **_logged_outcome(_response(current_memories=[_claim("f" * 64)])),
    )
    assert identifier is not None

    scratch = default_path()
    assert "pytest" in str(scratch), scratch
    assert identifier in [r["query_id"] for r in rows(scratch)]
    assert _user_log_size() == before, "a test call reached the real user log"


def _user_log_size() -> int:
    """Rows in the developer's own log, or 0 when there is none."""
    home = Path(os.path.expanduser("~/.mind-palace")) / "logs" / "queries.jsonl"
    if not home.exists():
        return 0
    return len(home.read_text().splitlines())
