"""How similar are two strings, when "similar" has to survive boilerplate.

Used by the write path's conflict hint and by `remember --supersedes` phrase
resolution. Both ask the same question: *which live claims say roughly this?*, and
both were answered with raw token overlap.

Raw overlap answers it badly when the corpus repeats itself. Measured over 2,000
claims built from twelve subject-and-fact templates, a query shares 60% of its
words with **166** of them, and the caller's next step is to look at the three that
come back. Most of those 166 are not noise: they are the same statement about a
different service, which is exactly what a duplicate check should surface. The
defect is not that they match, it is that a word every candidate carries is being
counted as evidence that it does.

Inverse document frequency is the standard correction, and it is the same reasoning
`memory_relevance` already applies with its shared-term set: a term all the
candidates carry cannot discriminate between them. Here the shared terms are a
*fraction* of the corpus rather than all of it, which is why an intersection misses
them and a document-frequency weight does not. Measured over the same 2,000 claims:

    measure                              fires on, per query    still finds the query's own claim
    raw overlap at 0.60                            166.3                              40/40
    idf-weighted at 0.60                           104.7                              40/40
    raw overlap at 0.85                             11.6                              40/40

The middle row is the shipped one. Raising the raw threshold instead looks
attractive on the last row and is the wrong fix: it would also stop reporting
genuine near-duplicates, and the test that matters is the right-hand column. A
measure that finds less but finds less of the *same* thing has not been improved.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

#: Words that carry no subject. Deliberately short: this is a "look at this"
#: signal, and a long stopword list would stop a hint from ever firing on a
#: sentence that happens to open with a preposition.
#:
#: Interrogatives are here because a question is not a claim. "What is the primary
#: datastore?" is about the datastore and is not about "what"; matching on the word
#: makes a question match every sentence in the corpus and match nothing useful.
#: `mindpalace history "What is the primary datastore?"` and
#: `mindpalace history "primary datastore"` return the same timeline because of this
#: line, and nothing else.
STOPWORDS = frozenset(
    """a an the of for to in on at by from with without and or as well that this these those
    is are was were be been being do does did done we our us you your i me my it its them
    they their use uses used have has had can could would should shall will may might must
    not no yes if then than so but about into over under again once here there all any both
    each few more most other some such only own same too very just now also what which who
    whom whose where when why how""".split()
)


def terms(text: str) -> frozenset[str]:
    """Content words of `text`: lowercased, digits and letters, stopwords gone."""
    return frozenset(w for w in re.findall(r"\w+", text.casefold()) if w not in STOPWORDS)


def document_frequencies(documents: Iterable[Iterable[str]]) -> dict[str, int]:
    """How many documents carry each term."""
    counts: dict[str, int] = {}
    for document in documents:
        for term in set(document):
            counts[term] = counts.get(term, 0) + 1
    return counts


def idf(term: str, frequencies: dict[str, int], total: int) -> float:
    """Smoothed inverse document frequency. Always positive, so an unseen term weighs."""
    return math.log((total + 1) / (frequencies.get(term, 0) + 1)) + 1.0


def overlap(probe: frozenset[str], document: frozenset[str]) -> float:
    """Plain share of the probe's terms the document also has. No weighting.

    Used for the opposite question from `weighted_overlap`: *is this claim about
    this at all*, rather than *which of these claims is closest*. The two want
    different measures and using one for both gets one of them wrong.

    Inverse document frequency gives a term seen in no document a very high weight,
    which is right when ranking candidates the query is already known to be about
    and wrong here: a query word that appears nowhere in the corpus is weak
    evidence, and weighting it heavily makes a paraphrase score near zero. Plain
    overlap dilutes it instead. Measured on the three-ADR fixture, the query
    "How did the datastore decision change?" carries three content words, one of
    which ("decision") is in no claim: weighted overlap scores the right pair of
    claims at 0.213 and the fallback declines, while plain overlap scores them at
    0.333 and it fires. Boilerplate is the risk plain overlap carries, and the
    caller of this function is the fallback that runs only when strict matching
    found nothing, where naming one subject beats reporting no history.
    """
    if not probe:
        return 0.0
    return len(probe & document) / len(probe)


def weighted_overlap(
    probe: frozenset[str],
    document: frozenset[str],
    frequencies: dict[str, int],
    total: int,
) -> float:
    """Share of the probe's *informative* weight that the document also carries.

    Zero only when the probe has no terms. A probe whose every term is shared by
    every candidate scores 1.0 against all of them, and that is the honest answer:
    the phrase genuinely cannot tell them apart, and a caller told "no claim is
    about this" would be told something false. `--supersedes` refuses with the
    matching ids in that case, which is more use than an absence.
    """
    if not probe:
        return 0.0
    weight = sum(idf(term, frequencies, total) for term in probe)
    if weight <= 0.0:
        return 0.0
    shared = sum(idf(term, frequencies, total) for term in (probe & document))
    return shared / weight
