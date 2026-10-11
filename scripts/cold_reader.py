"""Run the cold-reader experiment: an agent that has only the README.

    python scripts/cold_reader.py --task cli --model anthropic/claude-haiku-5.5

## What this is for

Every adoption number this project has is from a machine that has been reading
the source for weeks. The first-hour audit found two release blockers that way:
a wheel missing a module, and a write path that could not write. Both were
invisible to the test suite and to every document, because everyone who reads
this repository already knows things no document says.

A cold reader is the closest available approximation of a new user: it gets the
README and a task, and nothing else. Not the source, not the changelog, not a
person to ask.

## Why the prompt is shaped the way it is

The agent is given the README **in full, verbatim**, and a task. It is not given
the file list, the test suite, or a hint that anything is broken. Every tool call
is recorded with its wall clock, because "where it stalls" is the measurement --
a stall is a step that took a human being longer than it should have.

The model is a parameter, not a constant. A reader from a different family than
the one that built the system finds different things, because it does not share
the author's blind spots. `anthropic/claude-haiku-5.5` is the default for that
reason.

## What is measured, and what is not

The agent returns a **plan with commands and predicted output**. It does not get
a shell: an autonomous agent with a shell is a different experiment with a
different threat model, and this one runs unattended against a machine with
credentials on it.

So the honest measurement is: **does the README contain enough to produce a
correct plan, and how much of the plan is guesswork?** Concretely:

* which commands the reader names, and whether each one exists in the CLI;
* whether the reader invents an argument, a flag, or a file that is not there;
* whether the reader asks for help instead of guessing.

That is a lower bound on the real first-hour experience and it is directly
comparable across models. It is NOT a substitute for the first-hour audit in
`docs/first-hour-audit.md`, which executed the path end to end in a clean
container. Where the two disagree, the audit is right and this is wrong.

"Minutes lost" is estimated from the reader's own self-reported reasoning --
steps it describes as retrying, stalling on, or guessing at. It is the reader's
account, not an observed wall clock, and is labelled as such in the output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

TASKS = {
    "cli": (
        "You have just installed Mind Palace on a machine with PostgreSQL running "
        "at postgresql://mpadmin:secret@localhost:5432/mindpalace. Using ONLY the "
        "README you have been given, and a shell, store the statement "
        '"The production datastore changed from SQLite to PostgreSQL." and then '
        'answer the question "What datastore does production use?" with the '
        "correct answer. Report the exact commands you ran and their output. If a "
        "command fails, report the error verbatim and say what you tried next. "
        "Do not read any file outside the README."
    ),
    "sdk": (
        "Using ONLY the README you have been given, write and run a Python script "
        'that uses the Mind Palace Python SDK to store the statement "We will use '
        'PostgreSQL as the primary datastore." and then answer "Which datastore '
        'did we choose?". Report the script and its output. If something fails, '
        "report the error verbatim. Do not read any file outside the README."
    ),
    "mcp": (
        "Using ONLY the README you have been given, write and run a script that "
        "talks to the mindpalace-mcp server over stdio, lists its tools, and calls "
        'memory_recall to answer "What datastore does production use?" for the '
        "default corpus. Report the script and its output. If something fails, "
        "report the error verbatim. Do not read any file outside the README."
    ),
}

SYSTEM = (
    "You are a new developer who has never seen this project. You have a shell. "
    "You have been given the project's README and nothing else. Work only from "
    "it. Be concrete: run commands, show output, and say plainly when you are "
    "stuck. Never guess what a command does; run it."
)


def ask(model: str, readme: str, task: str, timeout: float = 600.0) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("cold_reader: OPENROUTER_API_KEY is not set")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"# README\n\n{readme}\n\n# TASK\n\n{task}"},
        ],
        "temperature": 0,
    }
    started = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            OPENROUTER,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
    elapsed = time.monotonic() - started
    response.raise_for_status()
    body = response.json()
    return {
        "model": body.get("model", model),
        "seconds": round(elapsed, 1),
        "content": body["choices"][0]["message"]["content"],
        "prompt_tokens": body.get("usage", {}).get("prompt_tokens"),
        "completion_tokens": body.get("usage", {}).get("completion_tokens"),
    }


COMMAND = re.compile(r"^\s*(?:\$|#)\s+(mindpalace[\w .\"'/-]*)", re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    readme = (ROOT / "README.md").read_text()
    result = ask(args.model, readme, TASKS[args.task])
    commands = COMMAND.findall(result["content"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "task": args.task,
                "requested_model": args.model,
                "model": result["model"],
                "seconds": result["seconds"],
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "commands_attempted": commands,
                "response": result["content"],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"model:   {result['model']}")
    print(f"seconds: {result['seconds']}")
    print(f"wrote:   {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
