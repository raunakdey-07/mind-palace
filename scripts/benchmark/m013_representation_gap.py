"""M013 representation-gap study: where does the missing vocabulary already live?

For every blind-set failure where gold is absent from the candidate set, ask a
narrow question: is the information the question needs ALREADY in the
authoritative corpus, just not on the claim that answers it?

Nothing here changes production code. This reads the corpus through the same
ingestion path the product uses and asks, for each gold claim, which of these
deterministic representations make the question reach it:

    claim_only     the authored claim text, as shipped
    +evidence      claim plus the text of its own evidence spans
    +doc_context   claim plus its document's front matter, title and path
    +both          all of the above

Every input is already present in L0/L1. No LLM, no model-generated vocabulary.
If a representation closes a gap, the information was present and merely exposed
badly. If none does, the gap is real and must be quantified, not assumed away.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))

VALID_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
URL = "postgresql://mpadmin:secret@localhost:5432/mindpalace"
FLOOR = 0.30


def _migrations(conn):
    for migration in sorted((ROOT / "migrations/versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def _session(conn):
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


def bootstrap_ci(values, iterations=2000, seed=12345):
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return (
        round(means[int(0.025 * len(means))], 4),
        round(means[min(len(means) - 1, int(0.975 * len(means)))], 4),
    )


async def collect_failure_sets() -> dict:
    """The blind failures, read from the frozen result artifacts."""
    out = {}
    baseline = json.load(open(ROOT / "docs/performance/heldout-v2-baseline.json"))
    out["v2"] = {
        p["qid"]: p for p in baseline["per_question"] if p["qid"] in baseline["unreachable"]
    }

    ceiling = json.load(open(ROOT / "docs/performance/m013-candidate-ceiling-heldout.json"))
    v1_unreachable = {r["qid"]: r for r in ceiling["unreachable_detail"]}
    from m0130_heldout import build_questions as v1_questions

    v1q = {q.qid: q for q in v1_questions()}
    out["v1"] = {
        qid: {
            "qid": qid,
            "text": v1q[qid].text,
            "key": v1q[qid].key,
            "category": v1q[qid].category,
            "shared": r.get("shared_term_count", 0),
            "gold_text": r.get("gold_claim_text"),
        }
        for qid, r in v1_unreachable.items()
        if qid in v1q
    }
    return out


async def build_corpus_state():
    """Ingest the corpus and return everything the representations may draw on."""
    from m0115_dataset import build_corpus

    from api.services.ingestion import IngestionService

    engine = create_async_engine(
        URL.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool
    )
    schema = "gap_" + uuid4().hex[:10]
    corpus_id = "c" * 64
    docs = {}
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            await conn.run_sync(_migrations)
            await conn.execute(
                text("INSERT INTO corpora(id, name) VALUES (:id, :name)"),
                {"id": corpus_id, "name": schema},
            )
            service = IngestionService()
            for doc in build_corpus():
                docs[doc.path] = doc.content
                async with _session(conn) as db:
                    await service._ingest_content(db, doc.content, doc.path, corpus_id)
            async with _session(conn) as db:
                r = await db.execute(
                    text(
                        "SELECT c.key, c.claim, c.value::text, d.path, "
                        "  (SELECT string_agg(e.quote, ' ' ORDER BY e.id) FROM memory_evidence e "
                        "     WHERE e.corpus_id = c.corpus_id AND e.version_id = c.version_id) "
                        "  AS evidence "
                        "FROM memory_claims c JOIN memory_documents d "
                        "  ON d.corpus_id = c.corpus_id AND d.id = c.memory_document_id "
                        "WHERE c.corpus_id = :cid"
                    ),
                    {"cid": corpus_id},
                )
                rows = [
                    {
                        "key": x[0],
                        "claim": x[1],
                        "value": x[2],
                        "path": x[3],
                        # No title column exists on memory_documents; the title
                        # lives in document front matter and is derived below.
                        "title": "",
                        "evidence": x[4] or "",
                    }
                    for x in r
                ]
        finally:
            await outer.rollback()
    await engine.dispose()

    for row in rows:
        front = docs.get(row["path"], "")
        head = front.split("---")[1] if front.startswith("---") and "---" in front[3:] else ""
        row["front_matter"] = " ".join(
            re.findall(r"[\w.\-]+", head.replace("title:", " ").replace("document_type:", " "))
        )
    return rows


REPRESENTATIONS = {
    "claim_only": lambda r: r["claim"],
    "claim_plus_evidence": lambda r: f"{r['claim']} {r['evidence']}",
    "claim_plus_doc_context": lambda r: f"{r['claim']} {r['front_matter']} {r['path']}",
    "claim_plus_evidence_and_context": lambda r: (
        f"{r['claim']} {r['evidence']} {r['front_matter']} {r['path']}"
    ),
}


def tokenize(value: str) -> set[str]:
    from api.services.memory_relevance import STOP

    out = set()
    for word in re.findall(r"[a-z][a-z0-9]+", value.casefold()):
        if word in STOP:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        out.add(word)
    return out


def lexical_reach(question: str, claims: list[dict], builder) -> list[str]:
    qt = tokenize(question)
    scored = []
    for row in claims:
        ct = tokenize(builder(row))
        scored.append((row["key"], len(qt & ct) / len(qt) if qt else 0.0))
    scored = [kv for kv in scored if kv[1] >= FLOOR]
    return [k for k, _ in sorted(scored, key=lambda kv: (-kv[1], kv[0]))]


def main() -> int:
    failures = asyncio.run(collect_failure_sets())
    claims = asyncio.run(build_corpus_state())
    by_key = {c["key"]: c for c in claims}

    result = {
        "commit": os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(),
        "corpus_claims": len(claims),
        "floor": FLOOR,
        "sets": {},
    }

    for set_name in ("v1", "v2"):
        rows = []
        for qid, f in sorted(failures[set_name].items()):
            gold = f["key"]
            claim_row = by_key.get(gold)
            if claim_row is None:
                rows.append({"qid": qid, "gold": gold, "error": "gold key not in corpus"})
                continue
            entry = {
                "qid": qid,
                "gold": gold,
                "category": f.get("category"),
                "question": f["text"],
                "claim_text": claim_row["claim"],
                "path": claim_row["path"],
                "has_evidence": bool(claim_row["evidence"].strip()),
                "front_matter_terms": sorted(tokenize(claim_row["front_matter"]))[:12],
            }
            qt = tokenize(f["text"])
            for name, builder in REPRESENTATIONS.items():
                reached = gold in lexical_reach(f["text"], claims, builder)
                ct = tokenize(builder(claim_row))
                entry[name] = reached
                entry[f"{name}_shared"] = sorted(qt & ct)
            rows.append(entry)

        all_qids = [r["qid"] for r in rows if "claim_only" in r]
        summary = {}
        for name in REPRESENTATIONS:
            newly = [r["qid"] for r in rows if r.get(name) and not r.get("claim_only")]
            still = [r["qid"] for r in rows if not r.get(name)]
            summary[name] = {
                "reachable": len(all_qids) - len(still),
                "of": len(all_qids),
                "newly_reachable": newly,
                "still_unreachable": still,
            }
        result["sets"][set_name] = {"failures": rows, "summary": summary}

    Path(ROOT / "docs/performance/m013-representation-gap.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )

    for set_name, block in result["sets"].items():
        print(f"\n=== {set_name.upper()} blind failures ({len(block['failures'])}) ===")
        for name, s in block["summary"].items():
            print(
                f"  {name:<34} reachable {s['reachable']}/{s['of']}  "
                f"newly {len(s['newly_reachable'])}  still {len(s['still_unreachable'])}"
            )
            if s["newly_reachable"]:
                print(f"      newly: {s['newly_reachable']}")
    print("\nwritten: docs/performance/m013-representation-gap.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
