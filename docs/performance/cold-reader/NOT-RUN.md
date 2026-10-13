# The cold-reader experiment did not run

`scripts/cold_reader.py` is written, linted, and committed. It has never
produced a result, and no number anywhere in this repository comes from it.

## Why

The experiment needs a language model to play the part of a new developer. The
only model credential on this machine is `OPENROUTER_API_KEY`, and that key has
a hard ceiling of **$0.00**:

```
$ curl -s https://openrouter.ai/api/v1/credits -H "Authorization: Bearer $OPENROUTER_API_KEY"
{"data":{"total_credits":0,"total_usage":0.06582244}}

$ curl -s -X POST https://openrouter.ai/api/v1/chat/completions ...
{"error":{"message":"Key limit exceeded (total limit). ...","code":403,
  "metadata":{"limit_source":"openrouter_key_limit"}}}
```

There is no second credential. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `TOGETHER_API_KEY`,
`DEEPSEEK_API_KEY` and `XAI_API_KEY` are all unset. `LLAMA_CPP_LOCAL_API_KEY` is
set but nothing is serving on any local port (checked 8080, 8000, 1234, 5000,
30000). The only model weights on disk are `all-MiniLM-L6-v2` and
`ms-marco-MiniLM-L-6-v2`, both of which are **embedders**: they map text to
vectors and cannot answer a question.

Raising the OpenRouter limit is a decision for the account owner, not for this
repository, and it is not one this branch should make.

## What this means for the round

Two phases are **not established**, and no substitute was used for either:

* **The cold-reader stalls.** `scripts/cold_reader.py` exists and would measure
  which commands a context-free agent invents, which flags it invents, and where
  it asks for help instead. Without a model, none of that is known.
* **Does it help models?** The downstream evaluation needs a frontier model, a
  mid-tier model, a small hosted model and a local open-weights model. None of
  the four tiers is reachable. Running the arms on a smaller reachable tier and
  reporting that as "the downstream result" would be substituting silently, which
  is exactly what the brief forbids.

## What *was* done instead, and what it does not replace

`docs/first-hour-audit.md` carries a **cold install** experiment: a container
with nothing preinstalled, following the README literally, measuring each step
and each stumble. That found two release blockers. It is a weaker instrument
than a context-free agent -- the auditor had read the repository before -- and it
is not a substitute for the stall data.
