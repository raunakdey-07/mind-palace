# Tag checklist

**Nothing in this file has been done.** It is the list of things that must be
true before a tag exists. The owner alone decides when to cut one.

Current state, measured 2026-10-13:

| | |
|---|---|
| `origin/main` | `07d0f91` |
| latest tag | **`v0.9.0`** |
| `pyproject.toml` version | `0.9.1` — **ahead of the last tag** |
| schema head in `v0.9.0` | `007`; `main` is at `010` |
| migrations shipped in a tag | `008`, `009`, `010` — **none** |

## Before a tag

### Version and identity

- [ ] `pyproject.toml` version matches the tag exactly.
- [ ] `CHANGELOG.md` has a dated entry for this version, not `[Unreleased]`.
- [ ] `docs/release-map.md` maps the milestone identifiers to this version.
- [ ] The release notes **lead with the upgrade notes**: migrations `008`–`010`,
      the `scripts/db/runtime_roles.sql` grant step, what a downgrade destroys,
      and that there were no tenants to preserve.
- [ ] `docs/security.md`'s applicability note names **this version**, not a
      branch. It currently names the 0.9.x train and says plainly that
      `v0.9.0` itself has neither `008`'s `{*}` handling nor `010`'s binding.

### Green, on a runner, at the tagged commit

- [ ] A CI run at the exact commit that will be tagged: `success`, every job
      green, nothing skipped.
- [ ] The `Run tests` job's junitxml is attached to that run, and
      `check_evidence --require-head` passed against it — i.e. the "N passed"
      figure in `STATUS.md` came from that run, not from a person.
- [ ] The skip gate passed: every skipped test is on
      `scripts/check_skips.py`'s allowlist, which currently holds exactly two
      entries and both are opt-in.
- [ ] `check_evidence --strict` passes locally against the same tree.

### The upgrade and downgrade claims, re-proven at this commit

- [ ] `v0.9.0` archive → `upgrade head` preserves every claim identity; no claim
      is lost; the old receipt still verifies offline; the Memory Pack is
      byte-identical with the clock pinned.
- [ ] `upgrade head` → `007` → `upgrade head` preserves the same, **and** a
      mutation is proven to be caught. A mutation that *survives* is a release
      blocker, not a documentation item.
- [ ] The downgrade-destroys-`memory_principals` claim in the CHANGELOG is
      still true at this commit. It is a destructive claim about a user's
      database; it is re-measured or it is not repeated.

### Packaging

- [ ] The wheel builds, and a **bare** install outside the source tree can run
      `mindpalace-proof verify` on a real receipt with no database and no
      network.
- [ ] `mindpalace_errors`, the runtime entry point and the verifier all ship.
- [ ] The MCP extra serves a real MCP session with all tools documented.

### The README's first command actually works

- [ ] Run it, in a clean container, exactly as printed.
- [ ] **Today it does not.** `pip install mindpalace-os` returns 404 on PyPI
      (checked 2026-10-13: the JSON API and the simple index both 404). The
      README has been changed to install from the repository instead, and the
      reason is written next to it. If the distribution is published before the
      tag, this item is satisfied by the bare name and the README should revert
      to it.
- [ ] `mind-palace` on PyPI is a **different project** (a note-taking app at
      0.1.0). Do not let anyone "fix" the install by adding a hyphen.

### Truth

- [ ] Every "N passed" figure in `STATUS.md` and `README.md` names the run that
      produced it.
- [ ] No document claims Mind Palace outperforms a named system. No
      cross-vendor harness has been run, so no such comparison may exist.
- [ ] Every figure that is development-only says so. Sealed scoring has never
      happened; held-out v1 is burned.
- [ ] `git diff v0.9.0..HEAD -- eval/ docs/performance/baseline-packs.json` is
      **empty**.

## After tagging

- [ ] `scripts/release/check_versioning.py` passes on the tag.
- [ ] A clean checkout of the tag passes `mindpalace --help`, the upgrade
      fixture and the five-minute path.
- [ ] The tag is annotated with the commit it names and nothing is force-moved
      afterwards.

## Not on this list, deliberately

* **1.0.0.** `docs/release/v1.0.0-readiness.md` is the checklist for that, and
  it currently reports **0 of 8 met**. This file is about tagging the 0.9.x
  train, which is a different decision at a different time.