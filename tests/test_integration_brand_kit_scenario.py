"""Brand-kit integration scenario over real HTTP (closes #125).

Walks the whole life of a content app's memory through the public SDK over real
HTTP, with row-level security and scope-token enforcement on. One corpus per
brand, one folder per campaign, human reviewers approving and rejecting copy.

Every test creates its own corpus via POST /api/corpora and passes it to every
call, per the shared test-fact contract. Steps 1-7 and 10 are one test function
(because they depend on each other); steps 8, 9 and 11 are separate.
"""

from __future__ import annotations

import asyncio
import statistics
import time

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from verilore_sdk import MemoryClientError, Verilore

from live_server import live_server  # noqa: F401,F811  module-scoped server fixture
from tenant_harness import RUNTIME_ROLE, apply_as_runtime, runtime_url

SCOPE = ["brand", "campaigns/spring"]

BRAND_GUIDE = (
    "---\nstatus: approved\n---\n\n# Trailhead brand guide\n\n## Voice\n\n"
    "- Our voice is warm, plain and confident.\n\n## Do not use\n\n"
    '- Never use the word "curated" in copy.\n- Never promise free shipping in ads.\n\n'
    "## Brainstorming\n\n- Maybe try a playful pirate theme someday.\n"
)

BRAND_SPECS = "# Trailhead product specs\n\n- The Trailhead 2 bike weighs 9.8 kilograms.\n"

SPRING_BRIEF = "# Campaign brief\n\n## Facts\n\n- The spring launch date is 14 March 2027.\n"

AUTUMN_BRIEF = "# Campaign brief\n\n## Facts\n\n- The autumn launch date is 9 September 2027.\n"

MODIFIED_GUIDE = (
    "---\nstatus: approved\n---\n\n# Trailhead brand guide\n\n## Voice\n\n"
    "- Our voice is warm, plain and direct.\n\n## Do not use\n\n"
    '- Never use the word "curated" in copy.\n\n'
    "## Brainstorming\n\n- Maybe try a playful pirate theme someday.\n"
)


def _ingest(srv, key, content, path, corpus="acme"):
    resp = httpx.post(
        f"{srv.url}/api/ingest/file",
        json={"content": content, "path": path, "corpus": corpus},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp


# ---------------------------------------------------------------------------
# C. Role check (first test in the module)
# ---------------------------------------------------------------------------


def test_role_properties():
    """The app connects as the least-privileged runtime role."""
    engine = apply_as_runtime(runtime_url())
    try:

        async def _run() -> tuple:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as conn:
                row = (
                    await conn.execute(
                        sa.text(
                            "SELECT current_user, rolsuper, rolbypassrls "
                            "FROM pg_roles WHERE rolname = current_user"
                        )
                    )
                ).one()
            await engine.dispose()
            return row

        row = asyncio.run(_run())
    finally:
        pass

    current_user, rolsuper, rolbypassrls = row
    assert current_user == RUNTIME_ROLE, f"current_user={current_user!r} != {RUNTIME_ROLE!r}"
    assert rolsuper is False, "runtime role is superuser"
    assert rolbypassrls is False, "runtime role has BYPASSRLS"


# ---------------------------------------------------------------------------
# Steps 1-7, 10: one test function (they depend on each other)
# ---------------------------------------------------------------------------


def test_brand_kit_lifecycle(live_server, capsys):  # noqa: F811
    key = live_server.keys["acme"]
    mp = Verilore(
        live_server.corpora["acme"], base_url=live_server.url, api_key=key, timeout=5, retries=1
    )

    # 1. Brand kit: ingest brand/guide.md and brand/specs.md.
    _ingest(live_server, key, BRAND_GUIDE, "brand/guide.md", corpus=live_server.corpora["acme"])
    _ingest(live_server, key, BRAND_SPECS, "brand/specs.md", corpus=live_server.corpora["acme"])

    # 2. Campaigns: ingest both briefs.
    _ingest(
        live_server,
        key,
        SPRING_BRIEF,
        "campaigns/spring/brief.md",
        corpus=live_server.corpora["acme"],
    )
    _ingest(
        live_server,
        key,
        AUTUMN_BRIEF,
        "campaigns/autumn/brief.md",
        corpus=live_server.corpora["acme"],
    )

    # 3. Generation context.
    p_full = mp.memory.pack(query="", budget=64000, path_prefix=SCOPE)
    p = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    blob = p.model_dump_json()
    assert "warm, plain and confident" in blob, f"step 3: voice missing: {blob}"
    assert 'Never use the word \\"curated\\"' in blob, f"step 3: curated rule missing: {blob}"
    assert "14 March 2027" in blob, f"step 3: spring date missing: {blob}"
    assert "9.8 kilograms" in blob, f"step 3: spec missing: {blob}"
    assert "9 September" not in blob, f"step 3: autumn date leaked: {blob}"
    assert "pirate" not in blob, f"step 3: brainstorming leaked: {blob}"
    assert (p.withheld_counts or {}).get(
        "draft", 0
    ) >= 1, f"step 3: expected at least one withheld draft, got {p.withheld_counts}"
    assert p.conflicts == [], f"step 3: unexpected conflicts: {p.conflicts}"
    assert p.truncated is False, f"step 3: 16000 pack truncated: {p.truncated}"
    assert {c.id for c in p.current_memories} == {
        c.id for c in p_full.current_memories
    }, "step 3: 16000 pack must keep every current claim of 64000"
    voice_id = next(c.id for c in p.current_memories if "warm, plain and confident" in c.claim)
    with capsys.disabled():
        packs = {}
        for budget in (4000, 8000, 16000, 64000):
            if budget == 16000:
                packs[budget] = p
            elif budget == 64000:
                packs[budget] = p_full
            else:
                packs[budget] = mp.memory.pack(query="", budget=budget, path_prefix=SCOPE)
            print(
                f"budget={budget} current={len(packs[budget].current_memories)} "
                f"changes={len(packs[budget].changes)} truncated={packs[budget].truncated} "
                f"json_chars={len(packs[budget].model_dump_json())}"
            )
        small = packs[4000]
        full_ids = {c.id for c in p_full.current_memories}
        kept = {c.id for c in small.current_memories}
        print(f"step 3 (budget=4000): kept_all={kept == full_ids}")

    # 4. Reviewer rejects copy.
    mp.memory.remember(
        statement="Headline: Ride Into Spring",
        key="spring.headline",
        scope="campaigns/spring",
        state="rejected",
    )
    p2 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    blob2 = p2.model_dump_json()
    assert "Ride Into Spring" not in blob2, f"step 4: rejected copy still in pack: {blob2}"
    p3 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE, include_states=["rejected"])
    found = next(
        (
            c
            for c in p3.current_memories
            if "Ride Into Spring" in c.claim and c.info_state == "rejected"
        ),
        None,
    )
    assert (
        found is not None
    ), f"step 4: rejected claim not found with include_states: {p3.model_dump_json()}"

    # 5. Reviewer corrects the voice.
    r = mp.memory.remember(
        statement="Our voice is warm, plain and direct.",
        key="brand.voice",
        scope="brand",
        supersedes_claim_id=voice_id,
    )
    p4 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    blob4 = p4.model_dump_json()
    assert any(
        "warm, plain and direct" in c.claim for c in p4.current_memories
    ), f"step 5: corrected voice missing: {blob4}"
    assert not any(
        "warm, plain and confident" in c.claim for c in p4.current_memories
    ), f"step 5: old voice still current: {blob4}"
    h = mp.memory.history(query="", path=r.path)
    assert any(
        "warm, plain and confident" in c.claim for change in h.changes for c in change.previous
    ), f"step 5: history previous missing old voice: {h.model_dump_json()}"

    # 6. Brand team edits the guide.
    _ingest(live_server, key, MODIFIED_GUIDE, "brand/guide.md", corpus=live_server.corpora["acme"])
    p5 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    assert not any(
        "free shipping" in c.claim for c in p5.current_memories
    ), f"step 6: free shipping still current: {p5.model_dump_json()}"
    h2 = mp.memory.history(query="", path="brand/guide.md")
    assert (
        "free shipping" in h2.model_dump_json()
    ), f"step 6: history missing free shipping: {h2.model_dump_json()}"

    # 7. Withdraw a remembered rule.
    mp.memory.remember(
        statement="Never mention competitors by name.",
        key="brand.rule.competitors",
        scope="brand",
    )
    p6 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    assert any(
        "Never mention competitors by name" in c.claim for c in p6.current_memories
    ), f"step 7: rule missing from current: {p6.model_dump_json()}"

    mp.memory.remember(
        statement="Never mention competitors by name.",
        key="brand.rule.competitors",
        scope="brand",
        state="rejected",
    )
    p7 = mp.memory.pack(query="", budget=16000, path_prefix=SCOPE)
    assert not any(
        "Never mention competitors by name" in c.claim for c in p7.current_memories
    ), f"step 7: rejected rule still current: {p7.model_dump_json()}"
    assert (p7.withheld_counts or {}).get(
        "rejected", 0
    ) >= 1, f"step 7: expected withheld rejected, got {p7.withheld_counts}"

    # 10. Retry safety: same rejected remember again -> UNCHANGED.
    r3 = mp.memory.remember(
        statement="Never mention competitors by name.",
        key="brand.rule.competitors",
        scope="brand",
        state="rejected",
    )
    assert r3.event == "UNCHANGED", f"step 10: retry event={r3.event!r}"


# ---------------------------------------------------------------------------
# Step 8: isolation
# ---------------------------------------------------------------------------


def test_isolation(live_server):  # noqa: F811
    key = live_server.keys["acme"]
    mp = Verilore(
        live_server.corpora["acme"], base_url=live_server.url, api_key=key, timeout=5, retries=1
    )

    for method, kwargs in (
        (mp.memory.pack, {"corpus": live_server.corpora["globex"]}),
        (mp.memory.current, {"corpus": live_server.corpora["globex"]}),
        (mp.memory.history, {"corpus": live_server.corpora["globex"]}),
    ):
        with pytest.raises(MemoryClientError) as exc:
            method(**kwargs)
        assert exc.value.status_code in (
            403,
            404,
        ), f"step 8: {method.__name__} on globex gave {exc.value.status_code}"


# ---------------------------------------------------------------------------
# Step 9: memory down
# ---------------------------------------------------------------------------


def test_memory_down():
    start = time.monotonic()
    vl_down = Verilore(
        "acme", base_url="http://127.0.0.1:19999", api_key="x", timeout=1, retries=1
    )
    try:
        vl_down.memory.pack(query="")
    except MemoryClientError as exc:
        elapsed = time.monotonic() - start
        assert exc.code == "transport_error", f"step 9: code={exc.code!r}"
        assert elapsed < 3, f"step 9: took {elapsed:.2f}s"
        return
    raise AssertionError("step 9: no error raised from dead server")


# ---------------------------------------------------------------------------
# Step 11: latency
# ---------------------------------------------------------------------------


def test_latency(live_server, capsys):  # noqa: F811
    key = live_server.keys["globex"]
    mp = Verilore(
        live_server.corpora["globex"], base_url=live_server.url, api_key=key, timeout=5, retries=1
    )

    WORD_LIST = [
        "the",
        "quick",
        "brown",
        "fox",
        "jumps",
        "over",
        "lazy",
        "dog",
        "bright",
        "silent",
        "river",
        "flows",
        "under",
        "old",
        "stone",
        "bridge",
        "cold",
        "wind",
        "blows",
        "through",
        "empty",
        "streets",
        "at",
        "night",
    ]

    def doc_text(i: int) -> str:
        words = [WORD_LIST[(i * 13 + j) % len(WORD_LIST)] for j in range(100)]
        return " ".join(words)

    start = time.monotonic()
    for i in range(200):
        content = f"# Note {i}\n\n{doc_text(i)}\n"
        _ingest(
            live_server,
            key,
            content,
            f"notes/n{i:03d}.md",
            corpus=live_server.corpora["globex"],
        )
    ingest_wall = time.monotonic() - start

    times = []
    for _ in range(20):
        t0 = time.monotonic()
        _ = mp.memory.pack(query="", budget=16000)
        times.append((time.monotonic() - t0) * 1000)

    qs = statistics.quantiles(times, n=100)
    p50 = qs[49]
    p95 = qs[94]
    with capsys.disabled():
        print(f"step 11: p50={p50:.1f}ms p95={p95:.1f}ms ingest_wall={ingest_wall:.2f}s")
    assert p95 < 1000, f"step 11: p95={p95:.1f}ms >= 1000ms"
