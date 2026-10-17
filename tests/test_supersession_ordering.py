"""Supersession ordering and unresolved references, both of which used to be fatal.

Two ways a repository in mid-migration could not be ingested at all:

* `ad-002.md` supersedes `adr/0001.md`, and it sorts first, because `-` is below
  `r`. `sorted()` put the superseder ahead of its target, the reference resolved
  against nothing, and the document was rejected. The order is now derived from
  the declarations rather than from the filename, so the author never has to
  rename a file to suit `strcmp`.

* A reference to a document that genuinely is not there. That is not a mistake --
  it is what a repository looks like halfway through a migration -- and aborting
  the whole document over it loses the claims it does state, which is the part a
  reader wants. It is a warning now, named on the report.

The distinction the code draws: a reference that resolves to *nothing* is a state.
A reference that resolves to *several* things is an author mistake, and guessing
would retire a live fact, so that still raises.
"""

import pytest

from api.services.ingestion import _reference_targets, _supersession_order

TRIPLE_BACKTICK = "```"


def _write(tmp_path, name, body):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path / name


def _order(tmp_path):
    """The order `sync_repo` would ingest `tmp_path` in, as relative paths."""
    return _supersession_order(tmp_path, sorted(tmp_path.rglob("*.md")))


def test_a_superseder_is_ingested_after_its_target(tmp_path):
    """The accident, named: `-` is below `r`, so `sorted()` gets this backwards."""
    _write(
        tmp_path,
        "ad-002.md",
        "# Two\n\n- The datastore is PostgreSQL 16.\n\nSupersedes: adr/0001.md\n",
    )
    _write(tmp_path, "adr/0001.md", "# One\n\n- The datastore is PostgreSQL 15.\n")
    assert _order(tmp_path) == ["adr/0001.md", "ad-002.md"]


def test_a_superseded_by_declaration_also_waits(tmp_path):
    """The other direction: the document named must already be there to resolve."""
    _write(
        tmp_path,
        "adr/0001.md",
        "# One\n\n- The datastore is PostgreSQL 15.\n\nSuperseded-by: ad/0002.md\n",
    )
    _write(tmp_path, "ad/0002.md", "# Two\n\n- The datastore is PostgreSQL 16.\n")
    assert _order(tmp_path) == ["ad/0002.md", "adr/0001.md"]


def test_an_abbreviated_path_resolves(tmp_path):
    """An author writes the shortest thing that identifies a document."""
    _write(
        tmp_path,
        "ad-002.md",
        "# Two\n\n- PostgreSQL 16 is the datastore.\n\nSupersedes: 0001-use-pg15.md\n",
    )
    _write(
        tmp_path, "examples/adr/0001-use-pg15.md", "# One\n\n- PostgreSQL 15 is the datastore.\n"
    )
    assert _order(tmp_path) == ["examples/adr/0001-use-pg15.md", "ad-002.md"]


def test_an_unrelated_repository_keeps_its_sorted_order(tmp_path):
    """Nothing declared, nothing reordered. The cost is one pass and no change."""
    for name in ("c.md", "a.md", "b.md"):
        _write(tmp_path, name, f"# {name}\n\n- The value is {name[0]}.\n")
    assert _order(tmp_path) == ["a.md", "b.md", "c.md"]


def test_a_cycle_keeps_every_file_rather_than_dropping_one(tmp_path):
    """A and B superseding each other is broken input, not a reason to lose a file."""
    _write(tmp_path, "a.md", "# A\n\n- The value is one.\n\nSupersedes: b.md\n")
    _write(tmp_path, "b.md", "# B\n\n- The value is two.\n\nSupersedes: a.md\n")
    assert sorted(_order(tmp_path)) == ["a.md", "b.md"]


def test_a_reference_to_nothing_leaves_both_documents_in_place(tmp_path):
    """Ordering cannot invent the missing file, so the cycle case covers both paths."""
    _write(
        tmp_path,
        "ad-002.md",
        "# Two\n\n- The datastore is PostgreSQL 16.\n\nSupersedes: 9999-gone.md\n",
    )
    _write(tmp_path, "adr/0001.md", "# One\n\n- The datastore is PostgreSQL 15.\n")
    assert sorted(_order(tmp_path)) == ["ad-002.md", "adr/0001.md"]


def test_a_claim_key_is_not_ordered_because_a_filename_cannot_resolve_it(tmp_path):
    """`architecture.postgres` names a claim, and the file holding it is not knowable."""
    _write(
        tmp_path,
        "ad-002.md",
        "# Two\n\n- The datastore is PostgreSQL 16.\n\nSupersedes: architecture.postgres\n",
    )
    _write(tmp_path, "adr/0001.md", "# One\n\n- The datastore is PostgreSQL 15.\n")
    assert _order(tmp_path) == ["ad-002.md", "adr/0001.md"]


def test_a_fenced_declaration_does_not_create_an_edge(tmp_path):
    """A worked example in a code block is an example, not a declaration."""
    body = (
        "# Two\n\n- The datastore is PostgreSQL 16.\n\n"
        f"{TRIPLE_BACKTICK}markdown\n---\nsupersedes: adr/0001.md\n---\n{TRIPLE_BACKTICK}\n"
    )
    _write(tmp_path, "ad-002.md", body)
    _write(tmp_path, "adr/0001.md", "# One\n\n- The datastore is PostgreSQL 15.\n")
    assert _order(tmp_path) == ["ad-002.md", "adr/0001.md"]


@pytest.mark.parametrize(
    "reference, expected",
    [
        ("adr/0001.md", "adr/0001.md"),
        ("adr/0002.md", "adr/0002.md"),
        ("examples/adr/0001.md", "examples/adr/0001.md"),
        # Two documents end in `0001.md`. A reference that could mean either is an
        # ambiguous reference, and resolving it anyway would land the supersession
        # on whichever happened to sort first.
        ("0001.md", None),
        ("architecture.postgres", None),
        ("9999-gone.md", None),
    ],
)
def test_reference_resolution(reference, expected):
    """Exact first, then a unique suffix."""
    rels = ["adr/0001.md", "examples/adr/0001.md", "adr/0002.md"]
    assert _reference_targets(rels, reference) == expected
