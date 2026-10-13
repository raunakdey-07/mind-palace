"""Assert that an upgrade from v0.9.0 preserved everything it promised.

    python scripts/release/assert_upgrade.py --before /tmp/fx090 --after /tmp/fx010

Run it with the NEW source tree on sys.path. The `--before` directory was written
by the OLD tree, so nothing here re-derives the old answer with new code.

## What is asserted, and what each failure would mean

| check | a failure would mean |
|---|---|
| the old receipt still verifies, offline | 008-010 changed what a recorded result means |
| the authoritative pack digest is unchanged | a migration rewrote the archive, not the schema |
| claim identities unchanged | document ids moved, so every receipt naming one points at nothing |
| the claim count is unchanged | rows were lost or duplicated |
| local mode as superuser, no credential | the upgrade forced a credential where there was none |
| the four read commands still work | the product reads a migration's output differently |

## Why the digest is the load-bearing check

The other checks can all pass while the archive has been quietly rewritten: the
same number of claims, the same keys, a different value. The authoritative digest
is taken over the canonical bytes of the pack, so it moves if anything the pack
is built from moves. A migration that is "additive" in the sense of adding tables
and losing nothing still fails here if it changed a single claim's text or
validity, which is exactly the class of regression worth catching before a tag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def run(command: list[str], env: dict | None = None) -> tuple[int, str]:
    import os

    result = subprocess.run(
        command, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _pin(pack: dict, valid_at: str) -> dict:
    """A copy of the pack with the one legitimately volatile field fixed."""
    out = json.loads(json.dumps(pack))
    state = out.get("state")
    if isinstance(state, dict):
        state["valid_at"] = valid_at
    return out


def _digest(pack: dict) -> str:
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--skip-runtime", action="store_true")
    args = parser.parse_args()
    before, after = Path(args.before), Path(args.after)

    failures: list[str] = []
    notes: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))
        if not ok:
            failures.append(f"{name}: {detail}")

    old_receipt = json.loads((before / "receipt.json").read_text())
    old_claims = json.loads((before / "claims.json").read_text())
    new_claims = json.loads((after / "claims.json").read_text())

    old_digest = old_receipt["receipt"]["receipts"][0]["proof"]["integrity"]["authoritative_digest"]

    # 1. The v0.9.0 receipt still verifies, with no database reachable.
    code, output = run(
        [sys.executable, "-m", "memory_proof_cli", "verify", str(before / "receipt.json")],
        env={"DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nothing"},
    )
    check("v0.9.0 receipt verifies offline after the upgrade", code == 0, output.strip()[:80])
    check("  ...and reports MATCH", "MATCH" in output and "VERIFIED" in output)

    # 2. Claim identities did not move.
    old_ids = {d["path"]: d["id"] for d in old_claims["documents"]}
    new_ids = {d["path"]: d["id"] for d in new_claims["documents"]}
    moved = sorted(p for p in old_ids if p in new_ids and old_ids[p] != new_ids[p])
    vanished = sorted(set(old_ids) - set(new_ids))
    check(
        "claim identities unchanged",
        not moved and not vanished,
        f"moved={moved} vanished={vanished}",
    )
    check(
        "no documents appeared",
        not (set(new_ids) - set(old_ids)),
        f"new={sorted(set(new_ids) - set(old_ids))}",
    )

    # 3. Nothing was lost.
    check(
        "claim count unchanged",
        old_claims["claim_count"] == new_claims["claim_count"],
        f"{old_claims['claim_count']} -> {new_claims['claim_count']}",
    )
    check(
        "corpora unchanged",
        old_claims["corpora"] == new_claims["corpora"],
        f"{old_claims['corpora']} -> {new_claims['corpora']}",
    )

    # 4. The authoritative digest is unchanged -- with `valid_at` pinned.
    #
    # `state.valid_at` is "now", so two exports seconds apart legitimately differ,
    # and the pack's canonical bytes include it. Comparing raw digests would
    # report a migration regression every single time. The product's own
    # determinism test pins it for the same reason
    # (tests/test_m016_json_contract.py:211), so the assertion here has to as
    # well, or it is measuring the clock rather than the archive.
    #
    # Everything else is compared as-is. A migration that changed a claim's text,
    # its validity or its evidence span survives the pin and fails the check.
    PINNED = "2026-01-01T00:00:00+00:00"
    old_pinned = _pin(json.loads((before / "pack.json").read_text()), PINNED)
    new_pinned = _pin(json.loads((after / "pack.json").read_text()), PINNED)
    differing = sorted(
        k for k in set(old_pinned) | set(new_pinned) if old_pinned.get(k) != new_pinned.get(k)
    )
    check(
        "pack bytes identical once the clock is pinned",
        old_pinned == new_pinned,
        f"differs in {differing}",
    )
    old_digest = _digest(old_pinned)
    new_digest = _digest(new_pinned)
    check(
        "pinned authoritative digest unchanged",
        old_digest == new_digest,
        f"{old_digest[:16]} -> {new_digest[:16]}",
    )
    notes.append(
        "the recorded digest moves with the clock by design; both sides are "
        "pinned and recomputed rather than trusting either record"
    )
    notes.append(f"alembic {old_claims['alembic_version']} -> {new_claims['alembic_version']}")

    if args.skip_runtime:
        print("\n(skipping the runtime and role checks)")
    else:
        # 5. The four commands still work.
        for command, label in (
            (["recall", "What datastore does production use?"], "recall"),
            (["explain", "What datastore does production use?"], "explain"),
            (["history", "infra.datastore"], "history"),
            (["corpora"], "corpora"),
        ):
            code, output = run(
                [sys.executable, "-m", "cli.main", *command],
                env={"MIND_PALACE_RUNTIME": "0"},
            )
            check(f"`mindpalace {label}` still works", code == 0, output.strip()[:90])

        # 6. Local mode as a superuser, with no credential at all.
        code, output = run(
            [sys.executable, "-m", "cli.main", "recall", "What datastore does production use?"],
            env={"MIND_PALACE_RUNTIME": "0", "MIND_PALACE_AUTH_MODE": "off"},
        )
        check("local mode as superuser, no credential", code == 0, output.strip()[:90])

    print()
    for note in notes:
        print(f"  note: {note}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nall upgrade assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
