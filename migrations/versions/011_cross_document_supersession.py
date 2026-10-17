"""Supersession across documents, and a `Key: Value` claim from plain Markdown.

Two limits were enforced at the database, and both were correct for what they
were guarding. They became wrong as soon as the archive had a second document.

**Supersession could not leave one document.** `memory_validate_supersession()`
required the target claim to sit in the same `memory_document_id` as the claim
replacing it, and in the immediate predecessor version. That makes a rewrite a
supersession and nothing else. So `adr-002.md` could not retire `adr-001.md`:
both would sit CURRENT under one key, in two documents, and `_query_result`
would report them as a CONFLICT. The archive would say "these sources disagree"
about a decision that was formally replaced weeks ago.

Two bases are added, and both keep the acyclicity argument the old rule used.
The old rule ruled out cycles by requiring an immediate predecessor, which
totally orders the edges. The new ones order by observation time instead: the
target must live in a version observed strictly before this one. `observed_at`
is `clock_timestamp()` on an append-only table, so that is a strict topological
order on its own, and it holds for a direct SQL write exactly as it holds for the
service.

`explicit_claim_id` names the target, so it carries no key constraint: a claim
read out of plain Markdown gets a positional key derived from its own heading
path, which a newer document cannot reproduce and should not have to.
`topic_key` does require the same key, because it identifies the target by key,
and retiring a claim about something else would be a mistake the archive can
prevent rather than report.

**Only two supersession bases existed.** `supersession_basis` was a two-value
CHECK. The two new ones are the whole reason for the column to exist beyond a
provenance label: without them the trigger above would accept the edge and the
CHECK would reject the row, and the failure would name a constraint rather than
the rule the caller broke.

The CHECK is dropped and recreated rather than altered, because the column was
created with an unnamed constraint and there is nothing to alter.
"""

from alembic import op

revision = "011_cross_document_supersession"
down_revision = "010_bind_unrestricted_scope"
branch_labels = None
depends_on = None

_BASES = (
    "'same_document_key'",
    "'single_claim_replacement'",
    "'explicit_claim_id'",
    "'topic_key'",
)

#: The two bases that may cross a document boundary. `topic_key` additionally
#: requires the target to carry the new claim's key.
_CROSS_DOCUMENT = ("explicit_claim_id", "topic_key")

_TRIGGER = """
    CREATE OR REPLACE FUNCTION memory_validate_supersession() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.supersedes_id IS NULL THEN
            RETURN NEW;
        END IF;

        IF NEW.supersession_basis IN ('explicit_claim_id', 'topic_key') THEN
            -- Same corpus (the foreign key guarantees it), a version strictly
            -- earlier than this one, and for a topic key, the same key. Strict
            -- ordering on an append-only observation timestamp is what rules out
            -- a cycle, and it does so for a hand-written INSERT as well as for
            -- the service.
            IF NOT EXISTS (
                SELECT 1 FROM memory_claims prior
                JOIN memory_versions pv
                  ON pv.corpus_id = prior.corpus_id AND pv.id = prior.version_id
                JOIN memory_versions nv
                  ON nv.corpus_id = NEW.corpus_id AND nv.id = NEW.version_id
                WHERE prior.corpus_id = NEW.corpus_id
                  AND prior.id = NEW.supersedes_id
                  AND prior.version_id <> NEW.version_id
                  AND pv.observed_at < nv.observed_at
                  AND (NEW.supersession_basis = 'explicit_claim_id'
                       OR prior.key = NEW.key)
            ) THEN
                RAISE EXCEPTION
                    'cross-document supersession requires an earlier observed claim in the same corpus'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        -- `004` behaviour, unchanged: edges may only point at a claim in the
        -- immediate predecessor version of the same document.
        IF NOT EXISTS (
            SELECT 1 FROM memory_claims prior
            JOIN memory_versions v ON v.corpus_id = NEW.corpus_id AND v.id = NEW.version_id
            WHERE prior.corpus_id = NEW.corpus_id AND prior.id = NEW.supersedes_id
              AND prior.memory_document_id = NEW.memory_document_id
              AND prior.version_id = v.predecessor_id
              AND (NEW.supersession_basis = 'single_claim_replacement' OR prior.key = NEW.key)
        ) THEN
            RAISE EXCEPTION
                'supersession requires an immediate predecessor claim in the same document'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$
"""


def upgrade() -> None:
    op.execute("ALTER TABLE memory_claims DROP CONSTRAINT memory_claims_supersession_basis_check")
    op.execute(
        "ALTER TABLE memory_claims ADD CONSTRAINT memory_claims_supersession_basis_check "
        f"CHECK (supersession_basis IN ({', '.join(_BASES)}))"
    )
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute(_TRIGGER)
    # A cross-document edge cannot be represented under the `004` rule, so the
    # downgrade says which rows would be lost instead of silently deleting them.
    count = op.get_bind().execute(
        "SELECT count(*) FROM memory_claims WHERE supersession_basis = ANY(ARRAY["
        + ",".join(f"'{b}'" for b in _CROSS_DOCUMENT)
        + "])"
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"{count} claim(s) supersede across documents; the previous trigger refuses "
            "those edges. Re-record them before downgrading."
        )
    op.execute("ALTER TABLE memory_claims DROP CONSTRAINT memory_claims_supersession_basis_check")
    op.execute(
        "ALTER TABLE memory_claims ADD CONSTRAINT memory_claims_supersession_basis_check "
        "CHECK (supersession_basis IN ('same_document_key', 'single_claim_replacement'))"
    )