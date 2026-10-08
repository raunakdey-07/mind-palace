# SPDX-License-Identifier: Apache-2.0
"""`MIND_PALACE_LEXICAL=1` must be a real mode, not a label.

Two properties, and both are asserted rather than described:

1. **It genuinely avoids the model stack.** Not "tells relevance.py to pretend the
   model is unavailable" -- it must not import `torch`, `transformers` or
   `sentence_transformers` at all, and it must be measurably faster than the
   semantic path because of it. A flag that only skipped a scoring step while still
   paying an 11-second import would be worse than useless: it would look like the
   model-free option and cost the same as the other one.

2. **It is the existing degraded path, not a second retriever.** `memory_query`
   already falls back to lexical relevance whenever the model cannot be loaded, and
   this mode takes that branch deliberately. One implementation, one set of
   acceptance gates, one set of authoritative semantics.

And one honest limit, asserted so it cannot be quietly forgotten: the two modes do
not always select the same keys. On the corpus below they differ on exactly the
question the M016 measurement predicted -- one where semantic ranking answers and
lexical ranking abstains. That difference is the reason this is documented as a
trade-off rather than sold as a substitute.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") or os.getenv("MEMORY_TEST_DATABASE_URL")),
    reason="needs a real PostgreSQL to read real memory",
)

HEAVY = ("torch", "transformers", "sentence_transformers")

#: The corpus, and the question pair that separates the two modes.
FACTS = {
    "ops.backups": "Backups run nightly at 02:00 UTC and are retained for 35 days.",
    "ops.oncall": "The on-call rotation is weekly, handing over on Wednesdays.",
    "security.secrets": "All secrets live in Vault under the platform namespace.",
}

#: Questions with real vocabulary overlap, so lexical mode demonstrably answers rather
#: than abstaining. A mode that only ever abstained would pass a naive smoke test
#: while being useless.
ANSWERABLE = "How often do we run backups?"

#: Semantic ranking answers this from the meaning of "when did the schedule change".
#: Lexical ranking shares no vocabulary with it and abstains. This is the measured,
#: intentional difference between the modes, kept here so the documentation and the
#: code cannot drift apart.
DIVERGENT = "When did we last change the backup schedule?"


@pytest.fixture(scope="module")
def corpus():
    """A corpus authored the way a user's is: through `remember`, so no embeddings."""
    from api.services import remember as remember_service
    from api.services.corpora import get_or_create_corpus
    from api.services.db import async_engine, session_scope

    name = f"m016-lexical-{os.getpid()}"

    async def build():
        async with session_scope() as db:
            row = await get_or_create_corpus(db, name)
        for key, claim in FACTS.items():
            await remember_service.remember(claim, corpus=name, corpus_id=row["id"], key=key)
        await async_engine.dispose()

    import asyncio

    asyncio.run(build())
    return name


def read(corpus: str, question: str, *, lexical: bool) -> dict:
    """One recall, in a fresh process, in the requested mode."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), MIND_PALACE_RUNTIME="0")
    env.pop("MIND_PALACE_LEXICAL", None)
    if lexical:
        env["MIND_PALACE_LEXICAL"] = "1"
    done = subprocess.run(
        [sys.executable, "-m", "cli.main", "recall", question, "--corpus", corpus, "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    import json

    return json.loads(done.stdout)


def _child(argv: list[str], lexical: bool) -> tuple[float, dict]:
    """Run `argv` in a child process and report what it imported and how long it took.

    A child, not this process, because the model stack is imported exactly once per
    interpreter and this one has already paid it for the other tests.
    """
    env = dict(os.environ, PYTHONPATH=str(ROOT), MIND_PALACE_RUNTIME="0")
    env.pop("MIND_PALACE_LEXICAL", None)
    if lexical:
        env["MIND_PALACE_LEXICAL"] = "1"
    program = (
        "import json, sys, time\n"
        "t = time.perf_counter()\n"
        f"sys.argv = {argv!r}\n"
        "from cli.main import app\n"
        "from typer.testing import CliRunner\n"
        "result = CliRunner().invoke(app, sys.argv)\n"
        "elapsed = time.perf_counter() - t\n"
        f"heavy = sorted(m for m in {HEAVY!r} if m in sys.modules)\n"
        "print(json.dumps({'exit': result.exit_code, 'elapsed': elapsed, 'heavy': heavy}))\n"
    )
    started = time.perf_counter()
    done = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    import json

    report = json.loads(done.stdout.strip().splitlines()[-1])
    report["wall"] = time.perf_counter() - started
    return report["wall"], report


def test_lexical_mode_does_not_import_the_model_stack(corpus):
    """The whole point: no torch, no transformers, no sentence_transformers.

    Checked by inspecting `sys.modules` in the process that actually ran the recall.
    Asserting on wall clock alone would pass on a fast machine and fail on a slow
    one; asserting on the import table is exact.
    """
    _, semantic = _child(["recall", ANSWERABLE, "--corpus", corpus], lexical=False)
    _, lexical = _child(["recall", ANSWERABLE, "--corpus", corpus], lexical=True)

    assert semantic["exit"] == 0, semantic
    assert lexical["exit"] == 0, lexical
    assert semantic["heavy"], (
        "the semantic path is expected to import the model stack; if this fails the "
        "environment has no model at all and the comparison is meaningless"
    )
    assert lexical["heavy"] == [], (
        f"lexical mode imported {lexical['heavy']}; it is supposed to touch no model "
        "stack, and anything else makes it a slower way of doing the same thing"
    )


def test_lexical_mode_is_faster_because_of_that(corpus):
    """The speed claim, measured in the same process shape on the same corpus."""
    _, semantic = _child(["recall", ANSWERABLE, "--corpus", corpus], lexical=False)
    _, lexical = _child(["recall", ANSWERABLE, "--corpus", corpus], lexical=True)
    assert lexical["elapsed"] < semantic["elapsed"] / 2, (
        f"lexical {lexical['elapsed']:.2f}s vs semantic {semantic['elapsed']:.2f}s -- "
        "the win comes entirely from not importing the model stack, so a small gap "
        "means something is still loading it"
    )


def test_lexical_mode_answers_and_abstains(corpus):
    """Model-free reading is real reading: it answers, and it declines.

    A mode that only abstained would pass a naive smoke test while being useless.
    """
    answered = read(corpus, ANSWERABLE, lexical=True)
    assert [c["claim"] for c in answered["current_memories"]] == [FACTS["ops.backups"]]
    assert answered["constraints"] == []
    assert answered["evidence"], "an answer must carry evidence in either mode"

    absent = read(corpus, "What is our incident response SLA?", lexical=True)
    assert absent["current_memories"] == []
    assert absent["constraints"] == ["NO_RELEVANT_MEMORY"]
    assert absent["evidence"] == [], "an abstention must not cite evidence"


def test_both_modes_return_the_same_public_contract(corpus):
    """`--json` is the public response either way, key for key.

    The mode is a retrieval choice; it must not become a second response shape that
    a script has to special-case.
    """
    semantic = read(corpus, ANSWERABLE, lexical=False)
    lexical = read(corpus, ANSWERABLE, lexical=True)
    assert sorted(semantic) == sorted(lexical)
    assert semantic["schema_version"] == lexical["schema_version"]
    for field in ("query", "corpus", "truncated", "budget_unit"):
        assert semantic[field] == lexical[field], field


def test_the_two_modes_select_differently_and_that_is_documented(corpus):
    """The measured difference, asserted so it cannot be quietly forgotten.

    This is the reason the mode is documented as a trade-off. If a future change made
    lexical and semantic agree here, this test should be *changed deliberately* with
    a reason -- not left to fail, and not deleted.
    """
    semantic = read(corpus, DIVERGENT, lexical=False)
    lexical = read(corpus, DIVERGENT, lexical=True)

    assert semantic[
        "current_memories"
    ], "semantic ranking is expected to answer this from meaning alone"
    assert lexical["current_memories"] == [] or lexical["constraints"], (
        "lexical ranking shares no vocabulary with this question; it is expected to "
        "abstain, which is a different answer and is documented as such"
    )


def test_lexical_mode_bypasses_a_running_runtime(corpus, tmp_path):
    """A runtime exists to hold the model; a model-free read must not involve it.

    The simplest architecture that satisfies every constraint: do not use it. The
    same `execute` runs in this process, so there is still one authoritative
    implementation and no protocol field to negotiate.
    """
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ, PYTHONPATH=str(ROOT), MIND_PALACE_STATE_DIR=str(state))
    env.pop("MIND_PALACE_LEXICAL", None)
    env.pop("MIND_PALACE_RUNTIME", None)

    from api import runtime as runtime_module

    # The runtime resolves its socket and token from the environment at call time, so
    # this process has to point at the same state directory the child will. Setting it
    # only in the child's env writes the token in one place and reads it in another.
    previous = os.environ.get("MIND_PALACE_STATE_DIR")
    os.environ["MIND_PALACE_STATE_DIR"] = str(state)
    runtime_module._ensure_token()
    process = subprocess.Popen(
        [sys.executable, "-m", "mindpalace" "_runtime"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not runtime_module.reachable():
            assert process.poll() is None, "the runtime exited while starting"
            time.sleep(0.2)
        assert runtime_module.reachable(), "runtime did not start"

        semantic_reply = runtime_module.call(
            {"op": "query", "request": {"corpus": corpus, "query": ANSWERABLE}},
            timeout=120.0,
        )
        assert semantic_reply["ok"]

        lexical_env = dict(env, MIND_PALACE_LEXICAL="1")
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli.main",
                "recall",
                ANSWERABLE,
                "--corpus",
                corpus,
                "--json",
            ],
            cwd=str(ROOT),
            env=lexical_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        import json

        assert json.loads(done.stdout)["current_memories"]
        assert "model-free" in done.stderr, (
            "a lexical read must announce itself on stderr, so a user is never "
            "mistaken about which mode produced the answer"
        )
    finally:
        try:
            runtime_module.call({"op": "shutdown"}, timeout=5.0)
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        if previous is None:
            os.environ.pop("MIND_PALACE_STATE_DIR", None)
        else:
            os.environ["MIND_PALACE_STATE_DIR"] = previous


def test_only_an_explicit_value_enables_the_mode():
    """A typo in a shell profile must not silently change what memory says.

    This mode is consequential: it selects different keys. Entering it by accident
    would mean answers that differ between terminals for no visible reason.
    """
    from api.services.retrieval_mode import lexical_requested

    for unset in (
        {},
        {"MIND_PALACE_LEXICAL": ""},
        {"MIND_PALACE_LEXICAL": "0"},
        {"MIND_PALACE_LEXICAL": "false"},
        {"MIND_PALACE_LEXICAL": "maybe"},
    ):
        assert lexical_requested(unset) is False, unset
    for on in ("1", "true", "TRUE", "yes", "on", " 1 "):
        assert lexical_requested({"MIND_PALACE_LEXICAL": on}) is True, on


def test_a_receipt_from_lexical_mode_verifies_offline(corpus, tmp_path):
    """The trust boundary is unchanged by the mode.

    A receipt proves what Mind Palace returned and how that response was represented.
    It does not prove the world is true, and choosing a different scorer does not
    weaken either half of that.
    """
    path = tmp_path / "lexical-receipt.json"
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT),
        MIND_PALACE_RUNTIME="0",
        MIND_PALACE_LEXICAL="1",
    )
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "receipt",
            ANSWERABLE,
            "--corpus",
            corpus,
            "-o",
            str(path),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert path.exists()

    from memory_receipt import verify_response_receipt

    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    checked = verify_response_receipt(payload["receipt"], payload["response"])
    assert checked["verified"], checked
    assert checked["receipts_checked"] >= 1, checked
    assert checked["failures"] == [], checked

    # And a tampered lexical receipt is rejected the same way a semantic one is: the
    # trust boundary is a property of the receipt, not of the scorer that produced it.
    forged = dict(payload)
    forged["response"] = dict(payload["response"])
    forged["response"]["current_memories"] = [
        dict(payload["response"]["current_memories"][0], claim="Backups never run at all.")
    ]
    assert verify_response_receipt(forged["receipt"], forged["response"])["verified"] is False
