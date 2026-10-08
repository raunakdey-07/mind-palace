# SPDX-License-Identifier: Apache-2.0
"""The runtime must not change a single authoritative answer.

A local runtime is a transport, not a second implementation. The only thing that
makes it safe is that it answers with byte-identical results, so this file compares
everything the product considers authoritative between the two paths and fails if
one of them differs by anything at all:

    claim identities, values, keys, status, path, version
    valid_from / valid_until / supersedes_id / evidence ids
    evidence text and character offsets
    constraints (which is where abstention lives)
    conflicts and their members
    sources, truncation
    the receipt, including its authoritative digest
    the Memory Pack digest

Queries cover the six shapes where M013 showed behaviour is tightly coupled:
ordinary, weakly related, absent, temporal, conflict, and multi-topic. Abstention
is compared as carefully as an answer, because a runtime that answers slightly more
is exactly as wrong as one that answers slightly less.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("MEMORY_TEST_DATABASE_URL")),
    reason="needs a real PostgreSQL to run a real runtime",
)


# A corpus with every shape the comparison has to survive: a supersession, a live
# conflict between two documents authoring the same key, and unrelated facts that a
# bad runtime would mix into the wrong answer.
CORPUS = {
    "datastore": [
        ("docs/one.md", "The production datastore is PostgreSQL 16."),
        ("docs/two.md", "The production datastore is CockroachDB."),
    ],
    "backups": [("docs/one.md", "Backups run nightly at 02:00 UTC and are kept 35 days.")],
    "oncall": [("docs/three.md", "The on-call rotation is weekly, handing over Wednesdays.")],
    "secrets": [("docs/three.md", "All secrets live in Vault under the platform namespace.")],
}

QUESTIONS = {
    "ordinary": "What datastore does production use?",
    "ordinary_two": "How often do we back up?",
    "weak": "datastore",
    "weak_two": "backups",
    "absent": "What is our incident response SLA?",
    "absent_two": "Who signs off a deployment?",
    "temporal": "What was the datastore before the migration?",
    "multi_topic": "Where are secrets and what is the on-call rotation?",
}

#: A question the corpus answers. Used wherever a test needs a real answer rather
#: than an abstention -- for the receipt and for time travel. Which questions answer
#: is retrieval behaviour and is deliberately not asserted here; what matters is that
#: the runtime and this process agree on all of them, answered or not.
SUPERSEDED_KEY_QUESTION = "What is the log retention policy?"

DOCUMENTS = {
    "docs/one.md": """---
title: One
date: 2026-02-01
claims:
  - key: datastore
    value: PostgreSQL 16
    claim: The production datastore is PostgreSQL 16.
    evidence: The production datastore is PostgreSQL 16.
  - key: backups
    value: nightly at 02:00 UTC, kept 35 days
    claim: Backups run nightly at 02:00 UTC and are kept 35 days.
    evidence: Backups run nightly at 02:00 UTC and are kept 35 days.
---

# One

The production datastore is PostgreSQL 16.

Backups run nightly at 02:00 UTC and are kept 35 days.
""",
    "docs/two.md": """---
title: Two
date: 2026-02-02
claims:
  - key: datastore
    value: CockroachDB
    claim: The production datastore is CockroachDB.
    evidence: The production datastore is CockroachDB.
---

# Two

The production datastore is CockroachDB.
""",
    "docs/three.md": """---
title: Three
date: 2026-02-03
claims:
  - key: oncall
    value: weekly, Wednesdays
    claim: The on-call rotation is weekly, handing over Wednesdays.
    evidence: The on-call rotation is weekly, handing over Wednesdays.
  - key: secrets
    value: Vault, platform namespace
    claim: All secrets live in Vault under the platform namespace.
    evidence: All secrets live in Vault under the platform namespace.
---

# Three

The on-call rotation is weekly, handing over Wednesdays.

All secrets live in Vault under the platform namespace.
""",
}


def fingerprint(response: dict) -> dict:
    """Everything authoritative, in one comparable value.

    Deliberately a plain dict of primitives so a failure names the field that
    differs instead of dumping two large objects at each other.
    """

    def claim(c: dict) -> tuple:
        return (
            c.get("id"),
            c.get("claim"),
            c.get("value"),
            c.get("key"),
            c.get("status"),
            c.get("path"),
            c.get("version_id"),
            c.get("valid_from"),
            c.get("valid_until"),
            c.get("supersedes_id"),
            tuple(c.get("evidence_ids") or ()),
        )

    return {
        "current": [claim(c) for c in response.get("current_memories") or []],
        "historical": [claim(c) for c in response.get("historical_memories") or []],
        "uncertain": [claim(c) for c in response.get("uncertain_memories") or []],
        "evidence": [
            (e.get("id"), e.get("text"), e.get("start_offset"), e.get("end_offset"))
            for e in response.get("evidence") or []
        ],
        "constraints": list(response.get("constraints") or []),
        "conflicts": [
            sorted(claim(c) for c in group.get("claims") or [])
            for group in response.get("conflicts") or []
        ],
        "sources": [s.get("path") for s in response.get("sources") or []],
        "truncated": response.get("truncated"),
        "receipt_digest": (response.get("receipt") or {}).get("memory_pack_digest"),
        "receipt_count": (response.get("receipt") or {}).get("receipt_count"),
    }


def pack_digest(response: dict) -> str | None:
    receipt = response.get("receipt") or {}
    return receipt.get("memory_pack_digest")


# ---------------------------------------------------------------------------
# The runtime, as a real subprocess against a real socket
# ---------------------------------------------------------------------------


#: Every comparison below pins the instant the answer is evaluated at.
#:
#: Without this the test is not testing the runtime. `valid_at` defaults to *now*,
#: so two calls a few milliseconds apart produce two different `state.valid_at`
#: values and therefore two different receipt digests -- correctly, because time
#: moved. That is the existing product contract and the runtime inherits it; pinning
#: the instant is what makes "same answer" a statement about the runtime rather than
#: about the clock.
PINNED = None  # set by the `seeded` fixture, once the corpus exists


@pytest.fixture(scope="module")
def live_runtime(tmp_path_factory):
    """A running runtime in its own state directory, stopped on teardown."""
    from api import runtime as runtime_module

    state = tmp_path_factory.mktemp("m016-state")
    os.environ["MIND_PALACE_STATE_DIR"] = str(state)
    runtime_module._ensure_token()

    process = subprocess.Popen(
        [sys.executable, "-m", "mindpalace" "_runtime"],
        cwd=str(ROOT),
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        import time

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if runtime_module.reachable():
                break
            if process.poll() is not None:
                pytest.fail(f"runtime exited: {process.stdout.read().decode()[-2000:]}")
            time.sleep(0.2)
        else:
            pytest.fail("runtime did not become reachable")
        yield runtime_module
    finally:
        with contextlib.suppress(Exception):
            runtime_module.call({"op": "shutdown"}, timeout=5.0)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            process.kill()
        os.environ.pop("MIND_PALACE_STATE_DIR", None)


@pytest.fixture(scope="module")
def seeded(live_runtime):
    """A corpus in the real database, written in-process before the comparison.

    Written in-process on purpose: the claim under test is that *reads* through the
    runtime match, so the corpus must not be something the runtime also produced.

    It contains one of each shape the comparison has to survive:

    * a **conflict** -- `docs/one.md` and `docs/two.md` author the same key with
      different values, so both are CURRENT and CONFLICTING rather than one
      superseding the other;
    * a **supersession** -- the same key written twice through `remember`, which
      puts both versions in one document and supersedes in place;
    * unrelated facts, so a wrong answer has somewhere to come from.

    Worth being explicit about one thing this fixture got wrong first: writing a
    `remember` under a key that an *authored document* also declares does not
    supersede it. `remember` supersedes within its own document, and a second
    document declaring the same key is a live conflict. That is the existing corpus
    contract, and the fixture now uses separate keys for the two shapes.

    The corpus is committed, because the runtime is a separate process and cannot
    see uncommitted rows. That has two consequences, both handled rather than ignored:

    * It cannot be a rolled-back fixture, so it outlives the module.
    * `delete_corpus` deliberately refuses a corpus with archive history -- which
      this one has -- so it is not deleted either. That guard is right and this test
      does not route around it.

    What it *can* do is author the corpus the way a real one is authored: through
    `remember` and documents written without an embedding model, so the chunks carry
    no vectors. `RetrievalService.search` selects `WHERE embedding IS NOT NULL` and is
    unscoped when given no corpus, so a corpus with no vectors is invisible to it.
    Leaving embedded chunks behind here made `test_search_semantics` fail on scores
    from a corpus it never created.
    """
    from api.services import remember as remember_service
    from api.services.corpora import get_or_create_corpus
    from api.services.db import async_engine, session_scope
    from api.services.ingestion import IngestionService

    name = f"m016-equiv-{os.getpid()}"

    async def build():
        async with session_scope() as db:
            row = await get_or_create_corpus(db, name)
        corpus_id = row["id"]
        # No vectors: see the docstring. Relevance still runs semantically, because a
        # read encodes the claims it needs and caches them.
        service = IngestionService(memory_enabled=True, require_embeddings=False)
        async with session_scope() as db:
            for path, content in DOCUMENTS.items():
                await service.ingest_file(content=content, path=path, corpus_id=corpus_id)
        for statement in (
            "Retention policy: logs are kept 90 days hot.",
            "Retention policy: logs are kept 30 days hot and 400 days cold.",
        ):
            await remember_service.remember(
                statement, corpus=name, corpus_id=corpus_id, key="ops.logs"
            )
        await async_engine.dispose()

    asyncio.run(build())

    # Pin the instant after the corpus exists, so every comparison below evaluates
    # the same state and the only variable left is where it was computed.
    from datetime import datetime, timedelta, timezone

    global PINNED
    PINNED = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    return name


def ask_in_process(corpus: str, question: str, **fields) -> dict:
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute

    request = MemoryRequest.model_validate(
        {"corpus": corpus, "query": question, "include_receipt": True, "valid_at": PINNED, **fields}
    )
    return asyncio.run(execute("query", request)).model_dump(mode="json")


def ask_runtime(runtime_module, corpus: str, question: str, **fields) -> dict:
    payload = {
        "corpus": corpus,
        "query": question,
        "include_receipt": True,
        "valid_at": PINNED,
        **fields,
    }
    reply = runtime_module.call({"op": "query", "request": payload}, timeout=120.0)
    assert reply.get("ok"), reply
    return reply["result"]


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_every_query_shape_is_byte_identical_through_the_runtime(seeded, live_runtime):
    """The whole point, stated as one test.

    Answered and abstained queries alike. If this fails, the runtime is not an
    optimisation and must not ship.
    """
    differences = []
    for name, question in QUESTIONS.items():
        direct = fingerprint(ask_in_process(seeded, question))
        via = fingerprint(ask_runtime(live_runtime, seeded, question))
        if direct != via:
            differences.append((name, question, direct, via))
    assert not differences, "\n".join(
        f"{name} ({question!r}):\n  in-process {d}\n  runtime     {v}"
        for name, question, d, v in differences
    )


def test_the_comparison_actually_exercised_abstention(seeded, live_runtime):
    """Guard against the equivalence test passing because everything abstained.

    A comparison where both sides return nothing proves nothing. This asserts the
    corpus produces an answer, a conflict and an abstention, so the equality above is
    comparing something real in both directions.
    """
    conflict = fingerprint(ask_in_process(seeded, QUESTIONS["ordinary"]))
    answer = fingerprint(ask_in_process(seeded, SUPERSEDED_KEY_QUESTION))
    absent = fingerprint(ask_in_process(seeded, QUESTIONS["absent"]))

    assert conflict["conflicts"], "the corpus must contain a live conflict"
    assert answer["current"], "an answerable question must return a claim"
    assert answer["evidence"], "an answer must carry evidence"
    assert answer["receipt_digest"], "an answered response must carry a receipt"
    assert absent["constraints"] == ["NO_RELEVANT_MEMORY"], absent
    assert absent["current"] == [], absent
    assert absent["evidence"] == [], "an abstention must not cite evidence"

    # A conflict carries no receipt, and that is the existing contract: there is no
    # single current memory to attest to. Asserted so a change to it is deliberate.
    assert conflict["receipt_digest"] is None, conflict
    assert not conflict["receipt_count"]


def test_a_temporal_query_is_identical_and_still_time_travels(seeded, live_runtime):
    """`as_of` through the runtime must answer from the past, not from now.

    The cutoff comes from the `history` operation rather than from the response,
    because the `query` intent returns only what is current: superseded claims live
    in `history`/`changes`, not in a query's `historical_memories`. Asking the archive
    where the change happened is also how the CLI's `history` command works.
    """
    present = fingerprint(ask_in_process(seeded, SUPERSEDED_KEY_QUESTION))
    assert any("400 days cold" in str(c[1]) for c in present["current"]), present

    cutoff = instant_between_versions(seeded)
    assert cutoff, "the supersession must be recorded with two instants"

    past_direct = fingerprint(ask_in_process(seeded, SUPERSEDED_KEY_QUESTION, as_of=cutoff))
    past_runtime = fingerprint(
        ask_runtime(live_runtime, seeded, SUPERSEDED_KEY_QUESTION, as_of=cutoff)
    )
    assert past_direct == past_runtime, (past_direct, past_runtime)
    assert any("90 days hot" in str(c[1]) for c in past_direct["current"]), past_direct
    assert (
        past_direct["current"] != present["current"]
    ), "asking as of a cutoff must not return the present state"


def instant_between_versions(corpus: str) -> str | None:
    """An instant at which the superseded version was still the current one.

    Anchored to the supersession itself -- the change that replaced a claim -- and
    then the midpoint between the replaced claim's own instant and the change's. Not
    the change's timestamp: `as_of` sets the validity instant and the observation
    cutoff together, so asking at exactly the observation instant can exclude the
    version being observed. A midpoint has no such edge, and needs no sleep.

    Scoped to the superseding change rather than to the corpus's earliest changes,
    which belong to other documents entirely.
    """
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute

    changes = asyncio.run(
        execute("history", MemoryRequest.model_validate({"corpus": corpus, "query": ""}))
    ).changes
    superseding = [c for c in changes if c.previous]
    if not superseding:
        return None
    change = min(superseding, key=lambda c: c.observed_at)
    replaced = change.previous[0].observed_at
    return (replaced + (change.observed_at - replaced) / 2).isoformat()


def test_the_receipt_is_the_same_receipt(seeded, live_runtime):
    """A receipt handed to someone must verify against what the runtime said.

    Compared on the whole bundle, not only the digest: the digest is the part a
    verifier reads, and the receipts inside it are what the digest is computed from.
    If these differ, a receipt exported through one path would not verify against a
    response produced by the other.

    The instant is pinned by `ask_*`, because a receipt covers `state.valid_at` and
    `valid_at` defaults to now. Two queries a millisecond apart legitimately produce
    two different digests; that is the product contract, not a runtime difference.
    """
    direct = ask_in_process(seeded, SUPERSEDED_KEY_QUESTION)
    via = ask_runtime(live_runtime, seeded, SUPERSEDED_KEY_QUESTION)
    assert pack_digest(direct), "an answered response must carry a receipt"
    assert pack_digest(direct) == pack_digest(via), (
        pack_digest(direct),
        pack_digest(via),
    )
    assert direct["receipt"]["receipts"] == via["receipt"]["receipts"]

    # And the receipt a person would hand over verifies against the response the
    # runtime actually returned -- the whole point of the file.
    from memory_receipt import verify_response_receipt

    checked = verify_response_receipt(via["receipt"], via)
    assert checked["verified"], checked


def test_the_memory_pack_is_the_same_memory_pack(seeded, live_runtime):
    """The portable artifact is the product's reproducibility claim.

    Two processes, two machines in principle. If the pack digest moved when the
    answer came from the runtime, "export a Memory Pack and rebuild it elsewhere"
    would stop being true.

    `valid_at` is pinned for the same reason as the receipt: the pack records the
    instant it was taken at, and two moments are two different packs.
    """
    from api.models.memory import MemoryRequest
    from api.services.memory_public import execute

    request = MemoryRequest.model_validate({"corpus": seeded, "query": "", "valid_at": PINNED})
    direct = asyncio.run(execute("pack", request)).model_dump(mode="json")
    reply = live_runtime.call({"op": "pack", "request": request.model_dump(mode="json")})
    assert reply.get("ok"), reply
    assert direct == reply["result"], "the runtime must produce a byte-identical Memory Pack"


def test_a_failure_is_translated_not_invented(seeded, live_runtime):
    """A runtime that fails must fail the way the product fails.

    The contract is that a failure reaches the user with the same code, the same
    wording and the same status whether it happened in the CLI's own process or in
    the runtime. A transport that invented its own error vocabulary -- or, worse,
    swallowed the error and returned an answer computed differently -- would be the
    exact failure this milestone exists to prevent.

    An unknown corpus is used because it fails for a reason the runtime cannot
    disguise: the authoritative lookup happens in the same code either way.
    """
    from mindpalace_sdk import MemoryClientError, MindPalace

    missing = f"m016-no-such-corpus-{os.getpid()}"

    # `create_if_missing=False` because a client that creates its corpus would never
    # see the failure this test is about.
    with pytest.raises(MemoryClientError) as through_runtime:
        MindPalace(name=missing, runtime=True, create_if_missing=False).recall("anything")

    with pytest.raises(MemoryClientError) as in_process:
        MindPalace(name=missing, create_if_missing=False).recall("anything")

    direct = in_process.value
    assert through_runtime.value.code == direct.code, (through_runtime.value.code, direct.code)
    assert through_runtime.value.status_code == direct.status_code
    assert through_runtime.value.message == direct.message
    assert through_runtime.value.code == "corpus_not_found", through_runtime.value


def test_a_runtime_from_a_different_build_is_refused(seeded, live_runtime, monkeypatch):
    """A protocol mismatch must be refused, never answered with other semantics.

    This is what stops a stale runtime from serving a v0.9.0 answer to a v0.10.0
    request after an upgrade. The refusal says how to fix it.
    """
    from api import runtime as runtime_module

    real = runtime_module.PROTOCOL
    reply = None
    try:
        # Speak the wrong protocol by hand, over the same socket.
        import asyncio

        async def ask():
            token = runtime_module._read_token()
            return await runtime_module._call_async(
                runtime_module.socket_path(),
                token,
                {"op": "status", "protocol": real + 99},
                5.0,
                1.0,
            )

        reply = asyncio.run(ask())
    finally:
        monkeypatch.setattr(runtime_module, "PROTOCOL", real)

    assert not reply.get("ok"), reply
    assert reply["error"]["code"] == "protocol_mismatch", reply
    assert "mindpalace runtime stop" in reply["error"]["message"], reply


def test_one_failure_one_exception_class_everywhere(seeded, live_runtime):
    """local, runtime and remote must raise the same type for the same condition.

    This is the asymmetry M016 found and declined to fix, because it was frozen by a
    contract test and outside that milestone's scope. v0.9.0 fixes it, and this is
    the test that holds it: a caller writes one `except` clause and it works however
    the request was served.
    """
    from mindpalace_sdk import MemoryClientError, MindPalace

    missing = f"m016-no-such-corpus-{os.getpid()}"

    # Each leg makes the same call over a different transport, so the only variable
    # is how the request was served.
    local_error = _capture(
        lambda: MindPalace(name=missing, create_if_missing=False).recall("anything")
    )
    runtime_error = _capture(
        lambda: MindPalace(name=missing, runtime=True, create_if_missing=False).recall("anything")
    )
    remote_error = _capture(
        lambda: MindPalace(base_url="http://127.0.0.1:1").memory.current(corpus=missing)
    )

    for label, error in (
        ("local", local_error),
        ("runtime", runtime_error),
        ("remote", remote_error),
    ):
        assert isinstance(error, MemoryClientError), (label, type(error))

    # The two legs that reached the service carry the service's own failure. The
    # remote leg aimed at a closed port, so it is a transport failure -- the class is
    # what matters there, and it is the same class.
    for label, error in (("local", local_error), ("runtime", runtime_error)):
        assert error.code == "corpus_not_found", (label, error.code)
        assert error.status_code == 404, (label, error.status_code)
    assert local_error.message == runtime_error.message


def _capture(call):
    """Run `call` and return the exception it raised, whatever type."""
    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is the assertion
        call()
    return raised.value


def test_a_bad_token_is_refused(seeded, live_runtime, monkeypatch):
    """The socket is on the filesystem; the token is the second check.

    A 0600 socket in a 0700 directory already excludes other users. This proves the
    token is actually verified, so a socket that ends up somewhere more permissive
    does not become an open door.

    The refusal comes back as an answer, not an exception: a peer that cannot
    authenticate gets a refusal, and the connection closes.
    """
    from api import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_read_token", lambda: "0" * 32)
    reply = runtime_module.call({"op": "status"}, timeout=5.0)
    assert not reply.get("ok"), reply
    assert reply["error"]["code"] == "forbidden", reply


def test_the_runtime_ships_in_the_wheel():
    """The CLI starts the runtime as `python -m mindpalace_runtime`.

    If that module is not packaged, `spawn()` fails silently on an installed
    package and every command quietly stays 6 seconds with nothing in the output to
    say why. This is the packaging boundary, asserted rather than hoped for.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules = pyproject["tool"]["setuptools"]["py-modules"]
    for required in ("mindpalace_runtime", "mindpalace_sdk", "memory_receipt"):
        assert required in modules, (
            f"{required} must ship in the wheel; without it the runtime never starts "
            "on an installed package"
        )


def test_the_runtime_is_never_required(seeded, live_runtime, monkeypatch):
    """With the runtime disabled, everything still works and is still correct.

    The runtime is an optimisation. A test suite, a CI job, and a deployment that
    turns it off must all be able to reach the same answers.
    """
    monkeypatch.setenv("MIND_PALACE_RUNTIME", "0")
    from api import runtime as runtime_module

    assert not runtime_module.enabled()
    assert not runtime_module.reachable()
    from mindpalace_sdk import MindPalace

    direct = fingerprint(
        MindPalace(name=seeded).recall(QUESTIONS["ordinary"]).model_dump(mode="json")
    )
    monkeypatch.delenv("MIND_PALACE_RUNTIME")
    assert direct == fingerprint(ask_in_process(seeded, QUESTIONS["ordinary"]))
