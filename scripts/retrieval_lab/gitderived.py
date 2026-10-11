"""A question set whose gold comes from git, not from an author.

    python scripts/retrieval_lab/run.py --set git-derived --arms A,C,F --out out.json

## Why this set exists

The other sets share a weakness: their gold was written by the same person who
wrote the retriever's evaluation targets, so they can share a blind spot with it.
This one cannot. Every label here is produced by running `git show` and reading
what came back. Nobody authored a question, and nobody decided what the right
answer is -- the repository's own history decides.

## What it is honestly worth

It is **passage-level only**. None of this repository's 66 documents carries an
authored `claims:` block, so there is no claim-level gold to speak of. Gold is
therefore a passage id of the form `CHANGELOG.md@v0.7.0#12`.

And it is **narrow**. The questions are template-shaped and come from two files
that change often. That makes it a good test of *whether history is
distinguishable at all*, and a poor test of general question quality. A system
that scores well here and badly on `heldout-v1` has learned about changelogs.

Both limits are stated here rather than discovered by a reader later.

## Three question shapes, all mechanical

| shape | slice | gold |
|---|---|---|
| "In which release did X first appear?" | historical | earliest CHANGELOG passage containing X |
| "What does the CHANGELOG record for release V?" | historical | passage under `## [V]` |
| "In which release did X first appear?" where X is in the README but in no changelog | absent | ABSENT |

The third shape is the one that matters most. A retriever that has learned
"return the most similar passage" will confidently return *something* for it,
and the phrase is guaranteed not to be there.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .corpus import Corpus, Passage, Question

ROOT = Path(__file__).resolve().parents[2]

#: The refs the corpus is built from. Every one of them is a released tag, so
#: the corpus is exactly what a user of that release would have had.
REFS = ("v0.5.1", "v0.6.0", "v0.7.0", "v0.8.0", "v0.9.0")

#: Files worth indexing: present at most refs, and changed often enough that
#: "what did this say then" has an answer that differs from "now".
FILES = ("CHANGELOG.md", "README.md", "docs/operations.md")

_HEADING = re.compile(r"^## \[(v[0-9][^\]]*)\]", re.MULTILINE)
_PHRASE = re.compile(r"\b[a-z]{5,12}(?: [a-z]{4,12}){2,3}\b")


def _git_show(ref: str, path: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout
    except Exception:  # noqa: BLE001 - a file absent at that ref is normal
        return None


def _sections(text: str) -> dict[str, str]:
    """Map release heading -> the body under it, up to the next `## `."""
    out: dict[str, str] = {}
    matches = list(_HEADING.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[match.group(1)] = text[start:end]
    return out


def _passages_for(ref: str, path: str, text: str) -> list[Passage]:
    """Split a file into paragraph-sized passages under a `## ` heading.

    Heading-aware on purpose. A release entry is the unit a question asks about,
    and a chunker that ignores headings returns a passage spanning two releases,
    which would make the gold ambiguous.
    """
    pieces: list[tuple[str, str]] = []
    current_heading = "(preamble)"
    buffer: list[str] = []
    for block in re.split(r"\n(?=## )", text):
        stripped = block.strip()
        if not stripped:
            continue
        first = stripped.split("\n", 1)[0]
        if first.startswith("## "):
            if buffer:
                pieces.append((current_heading, "\n".join(buffer)))
                buffer = []
            current_heading = first.lstrip("# ").strip()
        buffer.append(stripped)
    if buffer:
        pieces.append((current_heading, "\n".join(buffer)))

    out = []
    for index, (heading, body) in enumerate(pieces):
        for paragraph_index, paragraph in enumerate(
            [p.strip() for p in body.split("\n\n") if p.strip()]
        ):
            out.append(
                Passage(
                    passage_id=f"{path}@{ref}#{index}.{paragraph_index}",
                    path=f"{path}@{ref}",
                    text=f"{heading}\n{paragraph}",
                    order=index * 100 + paragraph_index,
                )
            )
    return out


def load_git_derived() -> tuple[Corpus, list[Question]]:
    """Build the corpus and the questions from the repository's own history."""
    contents: dict[tuple[str, str], str] = {}
    for ref in REFS:
        for path in FILES:
            text = _git_show(ref, path)
            if text:
                contents[(ref, path)] = text

    passages: list[Passage] = []
    for (ref, path), text in sorted(contents.items()):
        passages.extend(_passages_for(ref, path, text))

    questions: list[Question] = []
    final = _git_show(REFS[-1], "CHANGELOG.md") or ""
    final_sections = _sections(final)
    readme = _git_show(REFS[-1], "README.md") or ""

    # -- shape 1: "in which release did X first appear" ------------------------
    counter = 0
    for version, body in sorted(final_sections.items()):
        for phrase in dict.fromkeys(_PHRASE.findall(body)):
            first_ref = next(
                (
                    ref
                    for ref in REFS
                    if phrase in (_git_show(ref, "CHANGELOG.md") or "")
                ),
                None,
            )
            if first_ref is None:
                continue
            text = _git_show(first_ref, "CHANGELOG.md") or ""
            hits = [
                p
                for p in passages
                if p.path == f"CHANGELOG.md@{first_ref}" and phrase in p.text
            ]
            if not hits:
                continue
            counter += 1
            questions.append(
                Question(
                    qid=f"gd-hist-{counter:03d}",
                    text=f"In which release did Mind Palace first mention {phrase!r}?",
                    slice="historical",
                    key=None,
                    expect_state="HISTORICAL",
                )
            )
            questions[-1].gold_passage = hits[0].passage_id  # type: ignore[attr-defined]

    # -- shape 2: "what does the changelog record for release V" ---------------
    for version, _body in sorted(final_sections.items()):
        first_ref = next((ref for ref in REFS if f"## [{version}]" in
                          (_git_show(ref, "CHANGELOG.md") or "")), None)
        if first_ref is None:
            continue
        hits = [
            p
            for p in passages
            if p.path == f"CHANGELOG.md@{first_ref}" and f"[{version}]" in p.text
        ]
        if not hits:
            continue
        counter += 1
        question = Question(
            qid=f"gd-hist-{counter:03d}",
            text=f"What does the CHANGELOG record for release {version}?",
            slice="historical",
            key=None,
            expect_state="HISTORICAL",
        )
        question.gold_passage = hits[0].passage_id  # type: ignore[attr-defined]
        questions.append(question)

    # -- shape 3: absent, by construction --------------------------------------
    for phrase in dict.fromkeys(_PHRASE.findall(readme)):
        if phrase in final:
            continue
        counter += 1
        questions.append(
            Question(
                qid=f"gd-absent-{counter:03d}",
                text=f"In which release did Mind Palace first mention {phrase!r}?",
                slice="abstention",
                key=None,
                expect_state="ABSENT",
            )
        )
        if len([q for q in questions if q.slice == "abstention"]) >= 20:
            break

    # Attach passage gold. `Question` is frozen, so it is carried alongside.
    gold = {q.qid: getattr(q, "gold_passage", None) for q in questions}

    corpus = Corpus(
        claims=[],
        passages=passages,
        dataset_hash="git-derived",
        notes={
            "kind": "git-derived",
            "refs": list(REFS),
            "files": list(FILES),
            "gold": "passage-level only; no document in this repository carries "
                    "authored claims, so there is no claim-level gold here",
            "limit": "template-shaped questions from two frequently-changed files",
        },
    )
    return corpus, questions, gold
