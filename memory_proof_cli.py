# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Standalone proof CLI: mint and verify Verifiable Memory proofs.

Deliberately built on `argparse` from the standard library, not on the project's
Typer CLI. Typer is an OPTIONAL extra (``pip install mindpalace-os[cli]``), and a
verification command that fails without an optional dependency would defeat the
purpose: anyone who can install the package must be able to check a proof without a
server, a database, a model or a network. Nothing here imports typer, or anything
from `api` or `cli`.

    mindpalace-proof verify receipt.json          # written by `mindpalace receipt`
    mindpalace-proof verify proof.json --pack memory-pack.json
    mindpalace-proof prove  --pack memory-pack.json --claim-key architecture.postgres -o proof.json
    mindpalace-proof receipt --pack memory-pack.json --claim-key architecture.postgres \
-o receipt.json
    mindpalace-proof explain receipt.json --pack memory-pack.json

A file written by `mindpalace receipt` is self-contained: it carries the response
it describes, so verifying it needs no --pack and nothing but this package.

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
from memory_receipt import (
    ReceiptError,
    build_receipt,
    verify_receipt,
    verify_response_receipt,
    verify_trust,
)

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


def _load_target(path: str) -> tuple[dict, str]:
    """Accept a response bundle, a receipt, or a bare proof.

    The response bundle is what `mindpalace receipt` writes: the authoritative
    response next to the receipt describing it. Reading it here means someone who
    has only `pip install mindpalace-os` -- no CLI extra, no server, no database --
    can still check a receipt they were handed.
    """
    data = _read_json(path, "artifact")
    if isinstance(data, dict) and "response" in data and "receipt" in data:
        return data, "response"
    if isinstance(data, dict) and "receipt_version" in data:
        return data, "receipt"
    if isinstance(data, dict) and "proof_version" in data:
        return data, "proof"
    raise ProofError(
        f"{path!r} is not a Mind Palace receipt. It is neither a response bundle "
        "(written by `mindpalace receipt`), a receipt, nor a proof."
    )


def _verify_response_bundle(target: dict, trusted_digest: str | None) -> dict:
    """Check a `mindpalace receipt` file: the receipt against the response it covers."""
    bundle = target.get("receipt")
    artifact = target.get("response")
    if not isinstance(bundle, dict) or not isinstance(artifact, dict):
        raise ProofError("response bundle must hold a 'response' and a 'receipt' object")
    result = verify_response_receipt(bundle, artifact)
    trust = verify_trust({}, trusted_digest or "", bundle.get("memory_pack_digest", ""))
    verified = result["verified"]
    reasons: list[str] = []
    if not result["memory_pack_digest"]:
        reasons.append("the artifact no longer matches the digest the receipt recorded")
    if not result["query_binding"]:
        reasons.append("the receipt answers a different question than the artifact")
    for failure in result["failures"]:
        reasons.append(f"receipt {str(failure.get('receipt_id', ''))[:12]} does not verify")
    return {
        "verified": verified,
        "reasons": reasons,
        # One boolean per property, matching the other artifact kinds: the digest
        # is a fingerprint of the user's memory and does not belong in a verdict.
        "integrity": result["memory_pack_digest"],
        "provenance": not result["failures"],
        "temporal": not result["failures"],
        "supersession": not result["failures"],
        "receipts_checked": result["receipts_checked"],
        "authenticity": trust,
    }


def _render_verdict(
    verified: bool, reasons, integrity, provenance, temporal, supersession, authenticity, summary
) -> None:
    """One shape of output for every artifact kind, so the verdict reads the same."""
    if not verified:
        print("REJECTED")
        for reason in reasons:
            print(f"reason: {reason}")
        print()
        print("The artifact still represents a different state than the receipt describes.")
        return

    print("VERIFIED")
    print()
    print("Integrity:")
    print(f"  authoritative artifact: {'MATCH' if integrity else 'MISMATCH'}")
    print(f"  receipt: {'MATCH' if integrity else 'MISMATCH'}")
    print()
    print("Provenance:")
    print(f"  claim: {'MATCH' if provenance else 'MISMATCH'}")
    print(f"  evidence: {'MATCH' if provenance else 'MISMATCH'}")
    print(f"  document: {'MATCH' if provenance else 'MISMATCH'}")
    print()
    print("Temporal state:")
    print(f"  valid_at: {'MATCH' if temporal else 'MISMATCH'}")
    print(f"  supersession: {'MATCH' if supersession else 'MISMATCH'}")
    print()
    print("Trust:")
    if authenticity["authenticated"]:
        print("  authenticity: VERIFIED (trust anchor matched)")
    else:
        print("  authenticity: NOT ESTABLISHED (no trust anchor supplied)")
    print()
    for line in summary:
        print(line)
    print()
    print("This means the artifact still represents the state the receipt describes.")
    print("It does not establish that the original source was factually correct.")


def cmd_verify(args) -> int:
    target, kind = _load_target(args.target)

    if kind == "response":
        # No --pack: the response bundle carries its own authoritative artifact,
        # which is what makes one file enough to hand to someone else.
        result = _verify_response_bundle(target, args.trusted_digest)
        if args.trusted_digest and not result["authenticity"]["authenticated"]:
            result["verified"] = False
            result["reasons"].append("the recorded digest does not match the trust anchor")
        if args.json:
            print(
                json.dumps(
                    {"verified": result["verified"], "kind": kind, **result},
                    indent=2,
                    sort_keys=True,
                )
            )
            return EXIT_OK if result["verified"] else EXIT_REJECTED
        bundle = target["receipt"]
        summary = [
            f"query: {bundle.get('query') or '-'}",
            f"receipts checked: {result['receipts_checked']}",
            f"memory pack digest: {bundle.get('memory_pack_digest')}",
        ]
        _render_verdict(
            result["verified"],
            result["reasons"],
            result["integrity"],
            result["provenance"],
            result["temporal"],
            result["supersession"],
            result["authenticity"],
            summary,
        )
        return EXIT_OK if result["verified"] else EXIT_REJECTED

    if not args.pack:
        raise ProofError(
            f"--pack is required for a {kind}. A file written by `mindpalace receipt` "
            "carries its own artifact and needs no --pack."
        )
    pack = _load_pack(args.pack)
    actual_digest = pack.digest()

    if kind == "receipt":
        result = verify_receipt(target, pack)
        verified = result["verified"]
        reasons = (
            result["integrity"] + result["provenance"] + result["temporal"] + result["supersession"]
        )
        integrity_ok = not result["integrity"]
        provenance_ok = not result["provenance"]
        temporal_ok = not result["temporal"]
        supersession_ok = not result["supersession"]
        trust = verify_trust(result, args.trusted_digest or "", actual_digest)
        authenticity = trust
    else:
        verdict = verify(target, pack)
        verified = verdict.verified
        reasons = verdict.reasons
        integrity_ok = verdict.checks.get("proof_intact", False) and verdict.checks.get(
            "artifact_digest", False
        )
        provenance_ok = verdict.checks.get("identity", False) and verdict.checks.get(
            "evidence_ownership", False
        )
        temporal_ok = verdict.checks.get("temporal", False)
        supersession_ok = verdict.checks.get("status_consistency", False)
        trust = verify_trust({}, args.trusted_digest or "", actual_digest)
        authenticity = trust

    if args.json:
        print(
            json.dumps(
                {
                    "verified": verified,
                    "kind": kind,
                    "reasons": reasons,
                    "integrity": integrity_ok,
                    "provenance": provenance_ok,
                    "temporal": temporal_ok,
                    "supersession": supersession_ok,
                    "authenticity": authenticity,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_OK if verified else EXIT_REJECTED

    if not verified:
        print("REJECTED")
        for reason in reasons:
            print(f"reason: {reason}")
        return EXIT_REJECTED

    print("VERIFIED")
    print()
    print("Integrity:")
    print(f"  authoritative artifact: {'MATCH' if integrity_ok else 'MISMATCH'}")
    print(
        f"  {'receipt' if kind == 'receipt' else 'proof'}: "
        f"{'MATCH' if integrity_ok else 'MISMATCH'}"
    )
    print()
    print("Provenance:")
    print(f"  claim: {'MATCH' if provenance_ok else 'MISMATCH'}")
    print(f"  evidence: {'MATCH' if provenance_ok else 'MISMATCH'}")
    print(f"  document: {'MATCH' if provenance_ok else 'MISMATCH'}")
    print()
    print("Temporal state:")
    print(f"  valid_at: {'MATCH' if temporal_ok else 'MISMATCH'}")
    print(f"  supersession: {'MATCH' if supersession_ok else 'MISMATCH'}")
    print()
    print("Trust:")
    if authenticity["authenticated"]:
        print("  authenticity: VERIFIED (trust anchor matched)")
    elif args.trusted_digest:
        print("  authenticity: REJECTED (trust anchor does not match)")
    else:
        print("  authenticity: NOT ESTABLISHED (no trust anchor supplied)")
    if kind == "receipt":
        print()
        for line in _summarise_receipt(target):
            print(line)
    else:
        print()
        for line in _summarise(target):
            print(line)
    return EXIT_OK


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


def _summarise_receipt(receipt: dict) -> list[str]:
    """Identifying facts for a receipt, without dumping the embedded proof."""
    integrity = receipt.get("integrity", {})
    source = receipt.get("source", {})
    return [
        f"query: {receipt.get('query') or '-'}",
        f"answer: {receipt.get('answer', {}).get('claim') or '-'}",
        f"valid_at: {receipt.get('state', {}).get('valid_at') or '-'}",
        f"source: {source.get('path') or '-'} @ {source.get('document_version_id') or '-'}",
        f"memory pack digest: {integrity.get('memory_pack_digest')}",
        f"receipt id: {receipt.get('receipt_id')}",
    ]


def cmd_receipt(args) -> int:
    pack = _load_pack(args.pack)
    receipt = build_receipt(
        pack,
        args.claim_key,
        query=args.query,
        as_of=args.as_of,
        valid_at=args.valid_at,
        include_proof=not args.no_proof,
    )
    text = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"receipt written: {args.out}")
        print(f"receipt id: {receipt['receipt_id']}")
        print(f"answer: {receipt['answer']['claim']}")
    else:
        print(text)
    return EXIT_OK


def cmd_explain(args) -> int:
    """Show the recorded provenance and temporal basis for a proven memory.

    This is a provenance explanation, not a reasoning trace: no model internals,
    prompts or ranking scores are exposed.
    """
    target, kind = _load_target(args.target)
    pack = _load_pack(args.pack)

    if kind == "receipt":
        answer = target["answer"]
        state = target["state"]
        source = target["source"]
        evidence = target["evidence"]
        lineage = target["lineage"]
        temporal = target["temporal"]
        integrity = target["integrity"]
        query = target.get("query", "")
        selector = target["answer"]["claim_key"]
    else:
        proof = target
        entry = next(
            (e for e in proof["entries"] if e["claim"].get("status") == "CURRENT"),
            proof["entries"][0],
        )
        answer = entry["claim"]
        state = proof["state"]
        source = entry["document"]
        evidence = entry["evidence"]
        lineage = entry["lineage"]
        temporal = entry["temporal"]
        integrity = proof["integrity"]
        query = proof.get("query", "")
        selector = proof["claim_key"]

    # A proof entry and a receipt name the same things with different keys, and
    # printing one shape's keys against the other showed `claim: None` for every
    # receipt. Normalise once here, so the output below reads one vocabulary.
    text = answer.get("text") or answer.get("claim")
    claim_id = answer.get("id") or answer.get("claim_id")
    version = (
        answer.get("version_id") or source.get("document_version_id") or source.get("version_id")
    )

    print("ANSWER")
    print(f"  query: {query or '-'}")
    print(f"  claim key: {selector}")
    print(f"  claim: {text}")
    print(f"  value: {answer.get('value')}")
    print(f"  status: {answer.get('status')}")
    print()
    print("WHY THIS MEMORY")
    print(f"  claim id: {claim_id}")
    print(f"  version: {version}")
    print(f"  document: {source.get('document_id') or source.get('version_id') or '-'}")
    print(f"  path: {source.get('path') or '-'}")
    print()
    print("TIME")
    print(f"  as_of: {state.get('as_of') or '-'}")
    print(f"  valid_at: {state.get('valid_at') or '-'}")
    print(f"  observed_at: {temporal.get('observed_at') or '-'}")
    print(f"  valid_from: {temporal.get('valid_from') or '-'}")
    print(f"  valid_to: {temporal.get('valid_to') or '-'}")
    print()
    print("EVIDENCE")
    if not evidence:
        print("  (none recorded)")
    for row in evidence:
        heading = row.get("heading") or "-"
        body = row.get("text")
        print(f"  [{heading}] {body}")
        print(
            f"    id={row.get('id') or row.get('evidence_id')} "
            f"offset={row.get('start_offset')}-{row.get('end_offset')}"
        )
    print()
    print("HISTORY")
    print(f"  supersedes: {', '.join(lineage.get('supersedes') or []) or '-'}")
    print(f"  superseded by: {', '.join(lineage.get('superseded_by') or []) or '-'}")
    if temporal.get("in_force_at_pack_valid_at") is not None:
        print(f"  in force at valid_at: {temporal['in_force_at_pack_valid_at']}")
    print()
    print("RECEIPT")
    pack_digest = integrity.get("memory_pack_digest") or integrity.get("authoritative_digest")
    print(f"  memory pack digest: {pack_digest}")
    if integrity.get("proof_digest"):
        print(f"  proof digest: {integrity['proof_digest']}")
    if kind == "receipt" and target.get("receipt_id"):
        print(f"  receipt id: {target['receipt_id']}")
    if kind == "receipt":
        check = verify_receipt(target, pack)
        verdict_line = "VERIFIED" if check["verified"] else "REJECTED"
    else:
        verdict_line = verify(target, pack).render().splitlines()[0]
    print(f"  verifies against the supplied artifact: {verdict_line}")
    print()
    print("This explains the recorded provenance and temporal basis for the memory.")
    print("It does not establish that the original source was factually correct,")
    print("and without a trust anchor it does not establish authenticity either.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mindpalace-proof",
        description=(
            "Mint and verify Memory Receipts and proofs of recorded memory state. "
            "Verification needs no server, database, model or network."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify_parser = sub.add_parser(
        "verify", help="check a receipt or proof against the memory it describes"
    )
    verify_parser.add_argument(
        "target",
        help=(
            "a receipt written by `mindpalace receipt`, a receipt.json, or a "
            "proof.json; any of the three is accepted"
        ),
    )
    verify_parser.add_argument(
        "--pack",
        help=(
            "the authoritative Memory Pack. Not needed for a file written by "
            "`mindpalace receipt`, which carries its own artifact."
        ),
    )
    verify_parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable verdict"
    )
    verify_parser.add_argument(
        "--trusted-digest",
        help="a digest pinned out of band by the verifier; only with this can "
        "authenticity be established",
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
    explain_parser.add_argument("target", help="path to a receipt.json or proof.json")
    explain_parser.add_argument("--pack", required=True, help="path to the memory pack")
    explain_parser.set_defaults(func=cmd_explain)

    receipt_parser = sub.add_parser(
        "receipt", help="mint a Memory Receipt: what was returned, on what basis"
    )
    receipt_parser.add_argument("--pack", required=True, help="path to the memory pack")
    receipt_parser.add_argument("--claim-key", required=True, help="authored claim key")
    receipt_parser.add_argument(
        "--query", help="the query this receipt answers (defaults to the pack's query)"
    )
    receipt_parser.add_argument("--as-of", help="reconstruct the archive as of this instant")
    receipt_parser.add_argument(
        "--valid-at",
        help="the instant the answer must be valid at; selects which "
        "version of the key was authoritative then",
    )
    receipt_parser.add_argument(
        "--no-proof", action="store_true", help="omit the embedded proof to keep it minimal"
    )
    receipt_parser.add_argument("-o", "--out", help="write the receipt here (default stdout)")
    receipt_parser.set_defaults(func=cmd_receipt)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProofError, ReceiptError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
