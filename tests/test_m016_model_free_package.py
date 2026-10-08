# SPDX-License-Identifier: Apache-2.0
"""The package must import and run with no model stack installed at all.

`sentence-transformers` is a declared dependency, so this is not a claim about what a
normal install looks like. It is a guarantee about the *code*: nothing reaches for
`torch`, `transformers` or `sentence_transformers` at import time, and every product
command still works when those imports are made to fail.

Two reasons that guarantee is worth having:

* `MIND_PALACE_LEXICAL=1` exists to be usable in a lightweight or offline
  deployment. If the model stack were needed merely to *import* the package, the mode
  would be unusable in exactly the environment it was built for.
* It is what makes the dependency model safe to revisit later. v0.9.0 deliberately did
  not move `sentence-transformers` into an optional extra -- doing so would mean a
  plain `pip install mindpalace-os` stopped ranking semantically, which is a
  surprising behaviour change for every existing user. That decision is only safe
  because the code does not need the stack to run.

The block is installed as a meta-path finder, so the modules are genuinely
unimportable rather than merely unused.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BLOCKER = '''
import sys

BLOCKED = {"torch", "transformers", "sentence_transformers"}


class _Blocker:
    """Refuse the model stack, so an accidental import fails loudly."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"BLOCKED FOR TEST: {name} is not installed")
        return None


sys.meta_path.insert(0, _Blocker())
'''


def _run(argv: list[str], lexical: bool = False) -> tuple[int, str, str]:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    # The runtime is disabled deliberately, and not merely left unset. These tests
    # are about what happens *in this process*: with a warm runtime answering, the
    # child never touches a model at all, so blocking the model stack would prove
    # nothing and the degradation note would never be reachable.
    env["MIND_PALACE_RUNTIME"] = "0"
    env.pop("MIND_PALACE_LEXICAL", None)
    if lexical:
        env["MIND_PALACE_LEXICAL"] = "1"
    program = (
        BLOCKER
        + "\n"
        + "from typer.testing import CliRunner\n"
        + "from cli.main import app\n"
        + "import sys\n"
        + f"result = CliRunner().invoke(app, {argv!r})\n"
        + "heavy = sorted(m for m in ('torch','transformers','sentence_transformers') "
        + "if m in sys.modules)\n"
        # CliRunner captures what the command wrote to stderr instead of letting it
        # reach this process's own stderr, so it has to be re-emitted or the parent
        # cannot assert on it. `mix_stderr` was removed in click 8.2, so read it
        # defensively rather than assuming either shape.
        + "captured = getattr(result, 'stderr', '')\n"
        + "if not captured:\n"
        + "    captured = result.output\n"
        + "print('STDERR=' + (captured or '')[:4000])\n"
        + "print('OUTPUT=' + result.output[:4000])\n"
        + "print('HEAVY=' + repr(heavy))\n"
        + "sys.exit(result.exit_code)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # The child's real stderr, plus whatever the command wrote into its own, because
    # a diagnostic can legitimately come from either and the point is that the user
    # sees it, not which layer produced it.
    captured = "\n".join(part for part in (done.stderr, _section(done.stdout, "STDERR=")) if part)
    return done.returncode, done.stdout, captured


def _section(text: str, prefix: str) -> str:
    """The single line of `text` starting with `prefix`, without the prefix."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return ""


def test_the_cli_imports_with_no_model_stack():
    """`mindpalace --help` must not need torch. This is the packaging guarantee."""
    code, out, err = _run(["--help"], lexical=True)
    assert code == 0, err[-2000:]
    assert "HEAVY=[]" in out, out
    assert "remember" in out and "verify" in out


def test_init_works_with_no_model_stack():
    code, out, err = _run(["init"], lexical=True)
    # `init` verifies storage, so this needs a reachable database. A clean skip is
    # better than a false pass.
    if code != 0 and "could not reach" in (err + out).lower():
        pytest.skip("no database reachable for the storage check")
    assert code == 0, (out + err)[-2000:]
    assert "HEAVY=[]" in out, out


def test_reading_works_with_no_model_stack_in_lexical_mode():
    """The mode built for a lightweight deployment must work in one."""
    code, out, err = _run(
        ["recall", "How often do we run backups?", "--corpus", "default"], lexical=True
    )
    # Either it answered, or it correctly abstained. What must not happen is a crash
    # from a missing dependency.
    assert code == 0, (out + err)[-2000:]
    assert "HEAVY=[]" in out, (
        f"lexical mode imported the model stack: {out}. The mode is supposed to touch "
        "no model, and this is the condition it exists for."
    )
    assert "ModuleNotFoundError" not in err and "ImportError" not in err


@pytest.fixture(scope="module")
def unembedded_corpus():
    """A corpus whose claims have no cached vectors, so ranking must embed.

    This is the only condition under which a blocked model is guaranteed to
    degrade. A corpus whose vectors are already cached -- or one with nothing to
    rank at all -- answers without ever reaching for the model, so asserting the
    note against either of those would be testing the leftovers of a previous
    test rather than the behaviour. `mindpalace remember` writes claims without
    embeddings by design, so authoring the corpus this way is the honest setup.
    """
    from api.services import remember as remember_service
    from api.services.corpora import get_or_create_corpus
    from api.services.db import async_engine, session_scope

    name = f"m016-novec-{os.getpid()}"

    async def build():
        async with session_scope() as db:
            row = await get_or_create_corpus(db, name)
        for key, claim in (
            ("ops.backups", "Backups run nightly at 02:00 UTC and are retained for 35 days."),
            ("ops.datastore", "The production datastore is PostgreSQL."),
        ):
            await remember_service.remember(claim, corpus=name, corpus_id=row["id"], key=key)
        await async_engine.dispose()

    import asyncio

    asyncio.run(build())
    return name


def test_semantic_mode_degrades_instead_of_crashing_when_no_model_exists(unembedded_corpus):
    """A missing model is a documented degradation, not an import error.

    Ranking falls back to the lexical rung; authority does not change. Before the
    degradation note existed, a user could not tell a lexical answer from a semantic
    one, so stderr now says which one they got.

    Asserted, not merely permitted: a conditional assertion would pass whether or
    not the behaviour exists, and announcing the degradation is the whole point.
    """
    code, out, err = _run(["recall", "How often do we run backups?", "--corpus", unembedded_corpus])
    if code != 0 and "could not reach" in (out + err).lower():
        pytest.skip("no database reachable")
    assert code == 0, (out + err)[-2000:]
    assert "Traceback" not in err, err[-2000:]

    assert "could not be loaded" in err, (
        f"the read degraded without saying so: {err[-1500:]}. A silent fallback to "
        "lexical ranking is indistinguishable from a semantic answer, and the two "
        "select different keys."
    )
    assert "MIND_PALACE_LEXICAL" in err, err[-1500:]
    # And it must not have leaked into the answer. Checked against the command's own
    # stdout, not the harness's: `_run` deliberately re-emits the captured stderr on
    # the child's stdout so the parent can see it at all.
    assert "could not be loaded" not in _section(
        out, "OUTPUT="
    ), "the degradation note belongs on stderr; the answer must stay clean"
