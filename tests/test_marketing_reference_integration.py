"""Tests for the marketing-campaign reference integration.

They assert the four claims the example exists to demonstrate, and each is a
security claim rather than a demonstration of convenience:

* an approved brand fact is reusable by every campaign in its own workspace;
* a campaign's own facts do not reach its sibling campaign;
* an unapproved claim stays unapproved -- a generated draft does not become
  authoritative memory, and a rejection is recorded with its reason;
* one organisation's creator cannot read another organisation's memory.

The workflow's own role matrix is asserted in both authorization modes, so it
does not depend on Mind Palace's row-level policy being armed. The
cross-organisation read is also asserted through Mind Palace's real HTTP
surface, which is where the deployment boundary actually is.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "examples" / "marketing-campaign-memory")
)

from marketing_memory.generator import unsupported_claims  # noqa: E402
from marketing_memory.provision import BRAND_FACTS, UNAPPROVED_CLAIM, provision  # noqa: E402
from marketing_memory.service import PERMISSION, MarketingMemory  # noqa: E402
from marketing_memory.tenancy import Denied, brand_corpus  # noqa: E402
from tenant_harness import requires_tenant_db  # noqa: E402

pytestmark = [pytest.mark.asyncio, requires_tenant_db]

ALPHA = "ws-alpha"
BETA = "ws-beta"
PLATFORMS = ("blog", "linkedin", "facebook", "instagram")


@pytest.fixture(scope="session")
def memory_service_url():
    """A real Mind Palace, on a real socket, in a subprocess.

    Deliberately not `ASGITransport(app=app)`. That transport exercises the whole
    application and skips only the socket, which made it tempting -- and it is why
    the example used to import `api.main.app`. A consumer of Mind Palace connects
    to a URL, so the example connects to a URL too, and this suite proves it.

    The subprocess inherits `DATABASE_URL` and `MIND_PALACE_AUTH_MODE`, so the
    server under test is configured exactly as a deployment would be.
    """
    import contextlib
    import os
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # Set for the subprocess only, and never in `os.environ`.
    #
    # Two ways this went wrong before. First, a `monkeypatch` in the
    # function-scoped `seeded` fixture arrived after this session-scoped server
    # was already listening, so it ran unauthenticated and a cross-tenant read
    # returned 200. Writing to `os.environ` fixed that and broke something worse:
    # this fixture is session-scoped, so `required` stayed in the environment for
    # every later test file, and 248 tests that build an SDK client without an
    # `api_key` failed at once. The subprocess's environment is a dict; the
    # process environment is global. Keep them apart.
    environment = {
        **os.environ,
        "MIND_PALACE_AUTH_MODE": "required",
        "MIND_PALACE_DEPLOYMENT": "local",  # a test server is not a deployment
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = f"http://127.0.0.1:{port}"
    try:
        import httpx

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"the test server exited with {process.returncode}")
            try:
                if httpx.get(f"{url}/health/live", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            raise RuntimeError("the test server did not become live within 120s")
        yield url
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)
        if process.poll() is None:  # pragma: no cover - only on a hung shutdown
            process.kill()


@pytest.fixture
async def seeded(monkeypatch, memory_service_url):
    """Two organisations, provisioned, with the tenant boundary enforced.

    The mode is set through `monkeypatch` so it is restored for every other test
    in the suite, and so the isolation assertions below are not vacuous. It is
    applied before the server is asked about its own mode, and the server reads it
    from its own environment -- the same environment the demo would deploy into.
    """
    import sqlalchemy as sa

    from api.services.db import session_scope
    from marketing_memory.memory import MemoryClient
    from tenant_harness import require_auth_mode, sync_url
    from test_tenant_isolation import _provision, _reset

    require_auth_mode(monkeypatch)
    _provision()
    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            _reset(conn)
    finally:
        engine.dispose()

    async with session_scope() as db:
        result = await provision(db, memory_service_url)
        service = MarketingMemory(
            db,
            brand_client=MemoryClient(result.workspace_keys[ALPHA], client=result.http),
            campaign_client_for=lambda workspace, name: MemoryClient(
                result.workspace_keys[workspace], client=result.http
            ),
        )
        yield service, result
        await result.http.aclose()
        await db.execute(sa.text("DROP SCHEMA IF EXISTS marketing_app CASCADE"))
        await db.commit()
        _purge_example_corpora()


def _purge_example_corpora() -> None:
    """Erase the corpora and credentials the example provisioned.

    Deliberately its own cleanup rather than a call into the isolation suite's
    fixture: each suite owns its rows, and sharing the reset would make one
    suite's teardown depend on the other's data.
    """
    import sqlalchemy as sa

    from tenant_harness import sync_url

    engine = sa.create_engine(sync_url())
    try:
        with engine.begin() as conn:
            ids = [
                row.rstrip()
                for row in conn.execute(
                    sa.text(
                        "SELECT id FROM corpora WHERE name LIKE 'ws-%-brand' "
                        "OR name LIKE 'ws-%-c-%'"
                    )
                ).scalars()
            ]
            for table in (
                "memory_evidence",
                "memory_claims",
                "memory_chunks",
                "memory_snapshot_versions",
                "memory_snapshot_seals",
                "memory_snapshots",
                "memory_versions",
                "memory_documents",
                "memory_claim_embeddings",
            ):
                conn.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            for cid in ids:
                conn.execute(
                    sa.text(
                        "DELETE FROM chunks WHERE doc_id IN "
                        "(SELECT id FROM documents WHERE corpus_id = :cid)"
                    ),
                    {"cid": cid},
                )
                for table in (
                    "memory_evidence",
                    "memory_claims",
                    "memory_chunks",
                    "memory_snapshot_versions",
                    "memory_snapshot_seals",
                    "memory_snapshots",
                    "memory_versions",
                    "memory_documents",
                    "memory_claim_embeddings",
                ):
                    conn.execute(
                        sa.text(f"DELETE FROM {table} WHERE corpus_id = :cid"), {"cid": cid}
                    )
                conn.execute(
                    sa.text("DELETE FROM ingestion_manifest WHERE corpus_id = :cid"),
                    {"cid": cid},
                )
                conn.execute(sa.text("DELETE FROM documents WHERE corpus_id = :cid"), {"cid": cid})
            for table in (
                "memory_evidence",
                "memory_claims",
                "memory_chunks",
                "memory_snapshot_versions",
                "memory_snapshot_seals",
                "memory_snapshots",
                "memory_versions",
                "memory_documents",
                "memory_claim_embeddings",
            ):
                conn.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
            conn.execute(
                sa.text(
                    "DELETE FROM memory_principal_corpora WHERE principal_id IN "
                    "(SELECT id FROM memory_principals WHERE name LIKE 'service-ws-%')"
                )
            )
            conn.execute(sa.text("DELETE FROM memory_principals WHERE name LIKE 'service-ws-%'"))
            conn.execute(sa.text("DELETE FROM corpora WHERE id = ANY(:ids)"), {"ids": ids or [""]})
    finally:
        engine.dispose()


async def principal_for(service, result, workspace: str, role: str):
    return await service.principal(result.user_keys[f"{workspace}:{role}"], workspace)


async def test_an_approved_brand_fact_is_reusable_by_every_campaign_in_its_workspace(
    seeded,
):
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")

    for key in ("spring", "monsoon"):
        name = result.campaigns[f"{ALPHA}:{key}"].name
        facts = await service.campaign_facts(creator, name)
        assert facts, "each campaign should carry its own fact"
        draft = await service.generate_draft(creator, name, "blog")
        # Present in the generated body because the generator was handed it, not
        # because it happened to sit in the same corpus.
        assert BRAND_FACTS[ALPHA]["battery_life"] in draft.body
        assert BRAND_FACTS[ALPHA]["warranty"] in draft.body


async def test_a_campaigns_facts_do_not_reach_its_sibling(seeded):
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")

    spring = await service.campaign_facts(creator, result.campaigns[f"{ALPHA}:spring"].name)
    monsoon = await service.campaign_facts(creator, result.campaigns[f"{ALPHA}:monsoon"].name)
    assert all("coastal schools" in fact.text for fact in spring)
    assert not any("coastal schools" in fact.text for fact in monsoon)
    assert all("hill stations" in fact.text for fact in monsoon)
    assert not any("hill stations" in fact.text for fact in spring)

    draft = await service.generate_draft(
        creator, result.campaigns[f"{ALPHA}:spring"].name, "linkedin"
    )
    assert "coastal schools" in draft.body
    assert "hill stations" not in draft.body


async def test_an_unapproved_claim_stays_unapproved(seeded):
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    reviewer = await principal_for(service, result, ALPHA, "reviewer")
    spring = result.campaigns[f"{ALPHA}:spring"].name

    draft = await service.generate_draft(creator, spring, "blog")
    assert UNAPPROVED_CLAIM not in draft.body
    assert draft.state == "draft"

    # Asking the workflow to record it as an approved fact is a different
    # operation with a different role, not something generation can imply.
    with pytest.raises(Denied):
        await service.record_brand_fact(creator, "certification", UNAPPROVED_CLAIM)

    # Nor did the draft become authoritative memory by being written.
    brand = await service.approved_brand_facts(creator)
    assert not any(UNAPPROVED_CLAIM in fact.text for fact in brand)
    assert not any(draft.body in fact.text for fact in brand)

    # Approval is explicit, recorded, and carries the actor and the revision.
    submitted = await service.submit_for_review(creator, draft.id)
    approved = await service.approve(reviewer, submitted.id, "checked against sources")
    assert approved.state == "approved"
    assert approved.revision > submitted.revision
    decisions = await service.decision_history(approved.id)
    assert [d["action"] for d in decisions] == ["submit", "approved"]
    # Each transition is recorded against the revision it produced, so the trail
    # says *which* version was submitted and which was approved.
    assert [d["revision"] for d in decisions] == [submitted.revision, approved.revision]
    assert decisions[-1]["actor"] == f"{ALPHA}-reviewer"
    # And the asset records what it was generated from, pinned to a version.
    assert any(entry.startswith("battery_life@") for entry in approved.sources)


async def test_the_generator_reports_what_it_could_not_support(seeded):
    """The structural half of "an unapproved claim stays unapproved"."""
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    admin = await principal_for(service, result, ALPHA, "workspace_admin")
    draft = await service.generate_draft(creator, result.campaigns[f"{ALPHA}:spring"].name, "blog")

    # Every sentence in a draft comes from an approved fact, so with no facts at
    # all there is nothing the generator could have supported.
    assert draft.body
    approved = (await service.approved_brand_facts(creator)) + (
        await service.campaign_facts(creator, result.campaigns[f"{ALPHA}:spring"].name)
    )
    assert not unsupported_claims(draft, approved)

    # A claim nobody approved cannot be in the output, and asking for it is a
    # separate operation with its own role.
    assert UNAPPROVED_CLAIM not in draft.body
    with pytest.raises(Denied):
        await service.record_brand_fact(creator, "certification", UNAPPROVED_CLAIM)

    # An administrator can approve it. Only then does it become usable.
    await service.record_brand_fact(admin, "certification", UNAPPROVED_CLAIM)
    assert any(
        UNAPPROVED_CLAIM in fact.text for fact in await service.approved_brand_facts(creator)
    )


async def test_a_rejected_draft_is_recorded_and_regeneration_is_a_new_revision(seeded):
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    reviewer = await principal_for(service, result, ALPHA, "reviewer")

    draft = await service.generate_draft(
        creator, result.campaigns[f"{ALPHA}:monsoon"].name, "linkedin"
    )
    rejected = await service.reject(
        reviewer, (await service.submit_for_review(creator, draft.id)).id, "needs a source"
    )
    assert rejected.state == "rejected"
    assert rejected.reviewer_note == "needs a source"

    regenerated = await service.regenerate(creator, rejected.id)
    assert regenerated.revision > rejected.revision
    assert regenerated.state == "draft"

    # Regeneration adds a revision; it does not rewrite what happened.
    history = await service.decision_history(draft.id)
    assert any(d["action"] == "rejected" for d in history)


async def test_all_four_platform_variants_produce_reviewable_assets(seeded):
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    reviewer = await principal_for(service, result, ALPHA, "reviewer")
    spring = result.campaigns[f"{ALPHA}:spring"].name

    for platform in PLATFORMS:
        draft = await service.generate_draft(creator, spring, platform)
        assert draft.platform == platform
        assert draft.state == "draft"
        approved = await service.approve(
            reviewer, (await service.submit_for_review(creator, draft.id)).id, "ok"
        )
        assert approved.state == "approved"


async def test_the_role_matrix_is_enforced_by_the_server(seeded):
    """Each refusal, at each boundary. The frontend is not involved."""
    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    reviewer = await principal_for(service, result, ALPHA, "reviewer")
    spring = result.campaigns[f"{ALPHA}:spring"].name

    draft = await service.generate_draft(creator, spring, "blog")
    submitted = await service.submit_for_review(creator, draft.id)

    with pytest.raises(Denied):
        await service.approve(creator, submitted.id, "self-approval")
    with pytest.raises(Denied):
        await service.generate_draft(reviewer, spring, "blog")
    with pytest.raises(Denied):
        await service.record_brand_fact(creator, "k", "a fact a creator may not approve")
    with pytest.raises(Denied):
        await service.reject(creator, submitted.id, "no")
    with pytest.raises(Denied):
        await service.create_campaign(reviewer, "Reviewer's campaign", "not allowed")

    assert (await service.approve(reviewer, submitted.id, "fine")).state == "approved"
    # The matrix itself says the administrator holds every operation it grants.
    assert all(
        PERMISSION[operation] <= frozenset({"workspace_admin"})
        or "workspace_admin" in PERMISSION[operation]
        for operation in PERMISSION
    )


async def test_one_organisations_creator_cannot_read_another_organisations_memory(
    seeded, memory_service_url
):
    """The claim the whole example is built around.

    Two layers, both real. The workflow refuses because Beta is not a member of
    Alpha; and through Mind Palace's own surface -- over a socket, as a separate
    deployment would be -- Beta's credential is not scoped to Alpha's corpora, so
    naming one is refused there too.
    """
    service, result = seeded
    beta_creator = await principal_for(service, result, BETA, "content_creator")
    alpha_spring = result.campaigns[f"{ALPHA}:spring"].name

    with pytest.raises(Denied):
        await service.campaign_facts(beta_creator, alpha_spring)

    async with AsyncClient(base_url=memory_service_url) as client:
        response = await client.post(
            "/api/memory/query",
            headers={"Authorization": f"Bearer {result.workspace_keys[BETA]}"},
            json={"corpus": brand_corpus(ALPHA), "query": "battery"},
            # The first query through a cold server loads the embedding model.
            # Over a socket that is seconds, where an in-process transport hid it.
            timeout=120,
        )
    assert response.status_code == 404
    assert "ALPHA_BRAND" not in response.text


async def test_an_asset_id_from_another_workspace_does_not_resolve(seeded):
    """Parent/child mixing: a well-formed id from elsewhere is still not yours."""
    service, result = seeded
    alpha_creator = await principal_for(service, result, ALPHA, "content_creator")
    beta_reviewer = await principal_for(service, result, BETA, "reviewer")

    alpha_asset = await service.generate_draft(
        alpha_creator, result.campaigns[f"{ALPHA}:spring"].name, "blog"
    )
    assert alpha_asset.id.endswith(":blog:1")
    with pytest.raises(Denied):
        await service.submit_for_review(beta_reviewer, alpha_asset.id)
    with pytest.raises(Denied):
        await service.approve(beta_reviewer, alpha_asset.id, "not mine")


async def test_a_revoked_membership_stops_the_workflow_immediately(seeded):
    """Revocation is re-checked on every call, not cached on the client."""
    import sqlalchemy as sa

    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")
    assert await service.approved_brand_facts(creator)

    await service.db.execute(
        sa.text(
            "UPDATE marketing_app.memberships SET status = 'revoked' "
            "WHERE user_id = :u AND workspace_id = :w"
        ),
        {"u": f"{ALPHA}-content_creator", "w": ALPHA},
    )
    await service.db.commit()

    with pytest.raises(Denied):
        await service.approved_brand_facts(creator)


async def test_the_example_imports_no_mind_palace_internals_at_all():
    """The claim this example exists to make, checked mechanically.

    The integration is supposed to prove that an application can depend on Mind
    Palace's public contract alone. It did not, twice over:

    * an early version reached for `api.services.memory_public.execute` and
      `api.models.memory.MemoryRequest` and called that public. Any application
      needing those imports cannot be pointed at a different deployment, and
      breaks when the storage changes underneath a contract that did not;
    * the provisioning step and the demo each imported `api.main.app`, and this
      test then carried a written exception listing exactly those four lines.

    A test with an exception list is a test that has already decided what it is
    willing to miss. There is no exception list here any more: **no file in the
    example may import `api`, `cli`, `migrations`, or the SDK.** Corpus creation
    and credential issuance, which genuinely have no HTTP route, go through the
    documented `mindpalace` operator commands as subprocesses; the demo connects
    to a URL; and the suite starts a real server for it to talk to.
    """
    root = Path(__file__).resolve().parents[1] / "examples" / "marketing-campaign-memory"
    offenders = {}
    for path in sorted(root.rglob("*.py")):
        hits = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.search(r"^\s*(from|import)\s+(api|cli|migrations|mindpalace_sdk)\b", line)
        ]
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        "the example must reach Mind Palace over its documented interface only: " f"{offenders}"
    )


async def test_the_workflow_reaches_memory_over_http_and_not_in_process(seeded):
    """The client must be the only path, so the boundary is genuinely exercised.

    If any call bypassed HTTP it would run inside the same process as the
    application, where the authorization context is one `ContextVar` away from
    being set -- the exact condition the cross-tenant suites exist to catch, and
    one that would be invisible here.
    """
    import httpx

    service, result = seeded
    creator = await principal_for(service, result, ALPHA, "content_creator")

    calls: list[str] = []
    original = result.http.post

    async def spy(path, *args, **kwargs):
        calls.append(path)
        return await original(path, *args, **kwargs)

    result.http.post = spy
    try:
        assert await service.approved_brand_facts(creator)
        draft = await service.generate_draft(
            creator, result.campaigns[f"{ALPHA}:spring"].name, "blog"
        )
        assert draft.id
    finally:
        result.http.post = original

    assert calls, "the workflow made no HTTP call at all"
    assert all(path.startswith("/api/memory/") for path in calls), calls
    assert isinstance(result.http, httpx.AsyncClient)


async def test_the_client_refuses_a_corpus_the_credential_does_not_cover(seeded):
    """The refusal arrives as the public error contract, over HTTP.

    Not as an exception type invented here: a client in another language gets the
    same `404` with `corpus_not_found`, and that is the whole point of the
    envelope being the contract.
    """
    from marketing_memory.memory import MemoryClient, MemoryDenied

    service, result = seeded
    beta_credential = result.workspace_keys[BETA]
    beta_client = MemoryClient(beta_credential, client=result.http)

    with pytest.raises(MemoryDenied) as caught:
        await beta_client.current(brand_corpus(ALPHA))
    assert caught.value.status_code == 404
    assert caught.value.code == "corpus_not_found"
    assert "ALPHA_BRAND" not in caught.value.message


async def test_the_client_reports_an_unreachable_service_as_unavailable(seeded):
    """A transport failure is not a permission decision.

    Conflating them would let an application treat an outage as "you may not see
    this", and cache or display that as an authorization result.
    """
    import httpx

    from marketing_memory.memory import MemoryClient, MemoryDenied

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    offline = httpx.AsyncClient(
        transport=httpx.MockTransport(refuse), base_url="http://memory.invalid"
    )
    try:
        with pytest.raises(MemoryDenied) as caught:
            await MemoryClient("anything", client=offline).current("ws-alpha-brand")
    finally:
        await offline.aclose()

    assert caught.value.status_code == 503
    assert caught.value.code == "memory_unavailable"
