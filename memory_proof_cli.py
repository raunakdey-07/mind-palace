# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Standalone proof CLI: mint and verify Verifiable Memory proofs.

Deliberately built on `argparse` from the standard library, not on the project's
Typer CLI. Typer is an OPTIONAL extra (``pip install mindpalace-os[cli]``), and a
verification command that fails without an optional dependency would defeat the
purpose: anyone who can install the package must be able to check a proof without a
server, a database, a model or a network. Nothing here imports typer, or anything
from `api` or `cli`.

    mindpalace-proof verify proof.json --pack memory-pack.json
    mindpalace-proof prove  --pack memory-pack.json --claim-key architecture.postgres -o proof.json
    mindpalace-proof explain proof.json --pack memory-pack.json

Exit codes are stable and are part of the contract:

    0  verified, or a command completed successfully
    1  the proof was REJECTED against the artifact
    2  malformed input: unreadable file, bad JSON, unsupported version
    3  CLI usage error

Trust boundary, stated here because it is the easiest thing to overclaim:
verification establishes that the artifact still represents the recorded state the
proof describes. It does NOT establish that the original source was factually
truthful, and it is not a signature by any third party.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memory_pack import MemoryPack
from memory_proof import ProofError, build_proof, verify

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_BAD_INPUT = 2
EXIT_USAGE = 3


def _read_json(path: str, what: str):
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ProofError(f"cannot read {what} {path!r}: {exc.strerror or exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProofError(f"{what} {path!r} is not valid JSON: {exc}") from exc


def _load_pack(path: str) -> MemoryPack:
    try:
        return MemoryPack.from_dict(_read_json(path, "memory pack"))
    except ValueError as exc:  # PackError is a ValueError
        raise ProofError(f"memory pack {path!r} is not readable: {exc}") from exc


def _summarise(proof: dict) -> list[str]:
    """The identifying facts, not a dump of the whole proof."""
    integrity = proof.get("integrity", {})
    state = proof.get("state", {})
    versions = sorted({e.get("claim", {}).get("version_id") for e in proof.get("entries", [])})
    lines = [
        f"claim: {proof.get('claim_key')}",
        f"versions: {', '.join(v for v in versions if v) or '-'}",
        f"as_of: {state.get('as_of') or '-'}",
        f"valid_at: {state.get('valid_at') or '-'}",
        f"authoritative digest: {integrity.get('authoritative_digest')}",
        f"proof digest: {proof.get('proof_digest')}",
    ]
    return lines


def cmd_verify(args) -> int:
    proof = _read_json(args.proof, "proof")
    pack = _load_pack(args.pack)
    verdict = verify(proof, pack)

    if args.json:
        payload = {
            "verified": verdict.verified,
            "reasons": verdict.reasons,
            "checks": verdict.checks,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK if verdict.verified else EXIT_REJECTED

    if verdict.verified:
        print("VERIFIED")
        for line in _summarise(proof):
            print(line)
        return EXIT_OK

    print("REJECTED")
    for reason in verdict.reasons:
        print(f"reason: {reason}")
    return EXIT_REJECTED


def cmd_prove(args) -> int:
    pack = _load_pack(args.pack)
    proof = build_proof(pack, args.claim_key)
    text = json.dumps(proof, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"proof written: {args.out}")
        print(f"proof digest: {proof['proof_digest']}")
    else:
        print(text)
    return EXIT_OK


def cmd_explain(args) -> int:
    """Show the recorded provenance and temporal basis for a proven memory.

    This is a provenance explanation, not a reasoning trace: no model internals,
    prompts or ranking scores are exposed.
    """
    proof = _read_json(args.proof, "proof")
    pack = _load_pack(args.pack)
    state = proof.get("state", {})

    print("ANSWER")
    print(f"  query: {proof.get('query') or '-'}")
    print(f"  claim key: {proof.get('claim_key')}")
    print()
    print("STATE")
    print(f"  as_of: {state.get('as_of') or '-'}")
    print(f"  valid_at: {state.get('valid_at') or '-'}")
    print(f"  snapshot: {state.get('snapshot_id') or '-'}")
    print()

    for entry in proof.get("entries", []):
        claim = entry.get("claim", {})
        document = entry.get("document", {})
        temporal = entry.get("temporal", {})
        print("CLAIM")
        print(f"  key: {claim.get('key')}")
        print(f"  claim: {claim.get('text')}")
        print(f"  value: {claim.get('value')}")
        print(f"  status: {claim.get('status')}")
        print(f"  version: {claim.get('version_id')}")
        print()
        print("SOURCE")
        print(f"  document: {document.get('document_id') or '-'}")
        print(f"  path: {document.get('path') or '-'}")
        print(f"  version: {document.get('version_id') or '-'}")
        print(f"  observed_at: {temporal.get('observed_at') or '-'}")
        print()
        print("EVIDENCE")
        rows = entry.get("evidence", [])
        if not rows:
            print("  (none recorded)")
        for row in rows:
            heading = row.get("heading") or "-"
            print(f"  [{heading}] {row.get('text')}")
            print(
                f"    id={row.get('id')} offset={row.get('start_offset')}-{row.get('end_offset')}"
            )
        print()
        lineage = entry.get("lineage", {})
        print("HISTORY")
        print(f"  supersedes: {', '.join(lineage.get('supersedes') or []) or '-'}")
        print(f"  superseded by: {', '.join(lineage.get('superseded_by') or []) or '-'}")
        print(f"  valid_from: {temporal.get('valid_from') or '-'}")
        print(f"  valid_to: {temporal.get('valid_to') or '-'}")
        in_force = temporal.get("in_force_at_pack_valid_at")
        print(f"  in force at valid_at: {in_force}")
        print()

    print("PROOF")
    print(f"  authoritative digest: {proof.get('integrity', {}).get('authoritative_digest')}")
    print(f"  proof digest: {proof.get('proof_digest')}")
    verdict = verify(proof, pack)
    print(f"  verifies against the supplied artifact: {verdict.render().splitlines()[0]}")
    print()
    print("This explains the recorded provenance and temporal basis for the memory.")
    print("It does not establish that the original source was factually correct.")
    return EXIT_OK if verdict.verified else EXIT_REJECTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mindpalace-proof",
        description=(
            "Mint and verify Verifiable Memory proofs. Verification needs no server, "
            "database, model or network."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify_parser = sub.add_parser(
        "verify", help="check a proof against the authoritative Memory Pack it describes"
    )
    verify_parser.add_argument("proof", help="path to proof.json")
    verify_parser.add_argument(
        "--pack", required=True, help="path to the authoritative memory pack"
    )
    verify_parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable verdict"
    )
    verify_parser.set_defaults(func=cmd_verify)

    prove_parser = sub.add_parser("prove", help="mint a proof from an authoritative Memory Pack")
    prove_parser.add_argument("--pack", required=True, help="path to the memory pack")
    prove_parser.add_argument("--claim-key", required=True, help="authored claim key")
    prove_parser.add_argument("-o", "--out", help="write the proof here (default stdout)")
    prove_parser.set_defaults(func=cmd_prove)

    explain_parser = sub.add_parser(
        "explain", help="show the provenance, evidence and temporal basis of a proof"
    )
    explain_parser.add_argument("proof", help="path to proof.json")
    explain_parser.add_argument("--pack", required=True, help="path to the memory pack")
    explain_parser.set_defaults(func=cmd_explain)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProofError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
