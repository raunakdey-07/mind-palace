# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Which relevance path a read uses, and how the user asked for it.

Two modes, one implementation.

* **semantic** -- the default. Cosine similarity over the embedding model, with the
  acceptance gate applied to the semantic score. Used whenever a model is available.
* **lexical** -- the model-free rung that already exists as the degradation path.
  Normalized token overlap, scored through the *same* acceptance gates.

Lexical mode is not a second retriever. `memory_query` already falls back to it
whenever the model cannot be loaded, and `MIND_PALACE_LEXICAL=1` simply takes that
branch on purpose instead of by accident. One code path, one set of gates, one set
of authoritative semantics.

It is a real trade-off and is documented as one. Measured on a ten-fact corpus
across sixteen question shapes, lexical and semantic agreed on fifteen; the one
difference was an abstention the semantic ranking did not make. So lexical mode is
useful in a lightweight or offline deployment and is *not* guaranteed to select the
same keys. Nothing here weakens abstention to make the two agree, because an answer
that differs only in what it declines to say is still a wrong answer.

Deliberately import-light: this module must be readable by a process that has no
model stack installed at all.
"""

from __future__ import annotations

import os

#: Set to `1` to deliberately read without the embedding model.
ENV_VAR = "MIND_PALACE_LEXICAL"

#: What a user is told when they have asked for it. Short, factual, and on stderr so
#: it never contaminates an answer, a JSON document, or a pipe.
NOTE = f"retrieval: lexical (model-free, {ENV_VAR}=1)"


def lexical_requested(environ: dict | None = None) -> bool:
    """Whether this process was deliberately asked to read without the model.

    Only an explicit truthy value counts. An unset variable, an empty one, or
    anything unrecognised means semantic, because a mode this consequential should
    never be entered by accident or by a typo in a shell profile.
    """
    values = os.environ if environ is None else environ
    return values.get(ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}
