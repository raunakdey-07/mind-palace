# M007.1 Adjudication Artifact

This directory is research-only. It contains no gold labels yet.

## Current status

`DATA BLOCKED`: independent reviewer records have not been supplied. The
structural candidate generator remains a review-queue source, not ground truth.

`review_set.jsonl` contains 118 blind candidate items selected deterministically
from the development pool. It contains no adjudicated decisions and no resolver
predictions.

## Files

- `schema.json` — record fields and allowed values
- `instructions.md` — blind reviewer protocol
- `adjudication_template.jsonl` — empty JSONL template
- `review_set.jsonl` — reviewed records; currently empty

Do not add M006.75 predictions, ranking, similarity scores, or resolver output
to this artifact. The released v0.5.0 inputs and results are immutable.
