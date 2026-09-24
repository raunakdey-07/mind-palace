# M009 diagnostics inventory

Audit date: 2026-09-24

The editor reported a large pre-existing diagnostic set across the repository.
The inventory below classifies the findings by release impact instead of
trying to make the raw count zero.

| Category | Findings | Disposition |
|---|---|---|
| A. Correctness defect | None in the M009 runtime or tests. | No action. |
| B. Type/interface defect | Two local CLI issues in the release-adjacent path: the timeline query parameter type and a raw SQLAlchemy `execute("SELECT 1")` call. | Fixed in `cli/main.py`; focused tests and lint pass. |
| C. Security defect | None found in the M009 feed, cursor, logging, health, or adapter paths. | Covered by the M009 security tests and artifacts. |
| D. Maintainability problem | LSP suggestions for `datetime.UTC`, optional-annotation style, broad CLI exception boundaries, and nested context managers. | Kept where changing them would touch unrelated code or provide no concrete release benefit. |
| E/F/G. Environment, stale editor, or test-only findings | Research/evaluation type findings, dependency-stub findings, and existing pytest warnings. | Not M009 blockers. Full project-runtime tests pass. |
| H. Intentional pattern | CLI commands catch broad exceptions at user-facing boundaries; research benchmark code retains its existing interfaces. | Preserved to avoid changing unrelated behavior and research semantics. |
| I. False positive | Scanner matches caused by Markdown punctuation density, technical words such as “vector,” function definitions, and numeric measurement lines. | Rejected after context review. |
| J. Release-blocking unknown | None. | All M009 gates have executed evidence. |

## M009-specific result

`api/main.py`, `api/models/memory.py`, `api/routers/memory.py`,
`api/services/memory_feed.py`, and `mindpalace_sdk.py` have no editor errors
after the release pass. `api/services/memory_public.py` has two non-blocking
style suggestions in pre-existing code. Runtime checks pass for the validation
runner, and the M009 tests contain no correctness errors. The scanner's
high-confidence `smoke test` findings were real wording issues and were changed
to `functional check` or `quick check` in release-facing product documentation.
Its remaining findings are technical vocabulary,
Markdown structure, or source-code syntax matches, not product claims.

This inventory is an audit record, not a claim that unrelated legacy code has
been rewritten. The release boundary remains M009 product engineering and
release metadata; M006.75, M007, and M008 research artifacts are unchanged.
