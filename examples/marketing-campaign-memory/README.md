# Marketing campaign memory — reference integration

A thin FastAPI-shaped integration showing how a multi-user marketing application
would use Mind Palace as its memory layer, and what it has to get right before
that is safe.

This is **not** the marketing application. It is the part that decides whether
Mind Palace can be adopted by one, and it exists to make four claims testable:

1. An organisation's brand memory is reusable by every campaign in that
   organisation, and by nobody else's.
2. A campaign's own facts do not bleed into a sibling campaign, even when both
   campaigns discuss the same brand and product.
3. Generated content cannot become authoritative memory without an explicit,
   recorded approval.
4. One organisation's user cannot reach another organisation's memory, whatever
   they ask for.

## What is real and what is simulated

**Simulated:** content generation. `marketing_memory/generator.py` is a
deterministic stand-in for a model. No model is called, no network is used and no
paid service is involved, so the security assertions do not depend on a remote
service being reachable or on a model behaving the same way twice.

**What the deterministic generator proves** (integration and governance): corpus
mapping, authorization decisions, role matrix, archive/receipts/query surfaces,
tenant isolation, and the rule that generated content never becomes
authoritative memory without explicit approval. It proves these because they are
properties of the memory layer, not of model behaviour.

**What a real LLM evaluation harness tests** (output quality): whether an actual
model, given the Mind Palace context package, produces on-brand, constraint-
adherent copy. That is measured separately by the agency-readiness benchmark
(`benchmarks/agency_readiness/`); it is not what
this example asserts.

**Execution mode:** the example reaches Mind Palace over HTTP through the public
REST contract. It can run against an externally started service
(`MIND_PALACE_URL` / `MIND_PALACE_BASE_URL`) or against an in-process mounted
application (no socket, no port). Both paths exercise the same middleware,
authorization choke point and database policies; only the transport differs.

**Real:** the corpus mapping, the authorization decisions, the role matrix, the
archive, receipts and the query surfaces. Memory is reached over **HTTP, through
the public REST contract** — `httpx`, a bearer credential and a JSON body, to a
running Mind Palace at `MIND_PALACE_URL`. No direct SQL against Mind Palace's
tables, no import of `api.services`, no import of the server's ASGI application
object, no knowledge of its models or repositories.

That is the whole claim, so it is tested mechanically:
`test_the_example_imports_no_mind_palace_internals_at_all` fails if **any** file
under this directory imports `api`, `cli`, `migrations`, or the SDK.

Two earlier versions failed that test and were fixed rather than excepted:

* one called `api.services.memory_public.execute` in-process and called it
  public. It was not: an adopter needing those imports could not be pointed at a
  different deployment, and would break when the storage changed under a contract
  that did not;
* the provisioning step and the demo each imported `api.main.app` and drove it
  through `httpx.ASGITransport`, and the test carried a written list of exactly
  those four permitted lines. A test with an exception list has already decided
  what it is willing to miss.

Corpus creation and credential issuance have no HTTP route **by design** — a
network endpoint that mints a tenant boundary would be the thing this whole design
exists to avoid. They are now issued through the documented operator commands,
`mindpalace corpus create` and `mindpalace credential issue`, as subprocesses:
exactly what an integrating application's onboarding step would run. Onboarding is
an operator task; nothing a request handler performs uses it.

## Run it

Mind Palace has to be running first — this demo is a client of it, which is the
point.

```bash
# 1. The service, in one shell.
alembic -c migrations/alembic.ini upgrade head
MIND_PALACE_AUTH_MODE=required uvicorn api.main:app --host 127.0.0.1 --port 8000

# 2. The demo, in another.
python examples/marketing-campaign-memory/demo.py
```

`MIND_PALACE_URL` points the demo at the service; it defaults to
`http://127.0.0.1:8000`.

```bash
# To see the application layer's own refusals rather than Mind Palace's:
MIND_PALACE_AUTH_MODE=off python examples/marketing-campaign-memory/demo.py
```

The demo prints which authorization mode the *service* is running in, read from
`/health/ready` rather than from its own environment — so it cannot describe a
mode the service is not in. That is on purpose: a demo that quietly ran
permissively would be worse than no demo, because it would look like isolation.
It also refuses to run if the service answers `503`, rather than reporting a mode
it could not confirm.

The Mind Palace application is mounted in-process rather than started as a
service, so no port is needed. Every memory call still goes over HTTP through
the real middleware, the real authorization choke point and the real database
policies; only the socket is gone.

Against a real deployment, set the base URL instead:

```bash
MIND_PALACE_BASE_URL=http://mindpalace.internal:8000 python your_app.py
```

## How tenants map onto memory

```
workspace
    ├── brand memory corpus        ws-<workspace>-brand
    └── campaign
            └── campaign corpus    ws-<workspace>-c-<campaign>
```

The mapping is a pure function of records the application owns
(`marketing_memory/tenancy.py`). **No method in the workflow accepts a corpus
name.** A client that sends one has nowhere for it to land, which is why a
cross-tenant attempt fails at the first server-side resolution rather than at
some later filter.

Brand memory is deliberately shared inside a workspace: it is the vocabulary and
the approved product facts that every campaign in that organisation may draw on.
Campaign memory is not shared, not even between two campaigns of the same brand,
because a fact recorded for one campaign ("the spring range targets coastal
schools") is not true of its sibling.

## Ownership model

| Resource                  | Owner                          | Who may read it                       |
| ------------------------- | ------------------------------ | ------------------------------------- |
| Brand memory              | the workspace                  | members of that workspace             |
| Campaign brief, facts     | the campaign                   | members of the workspace              |
| Draft assets              | the campaign                   | members of the workspace              |
| Review decisions          | the campaign                   | members of the workspace              |
| Application records       | the workspace                  | members of that workspace             |
| Mind Palace credentials   | the deployment operator        | whoever holds the secret              |

Collaborators inside a workspace are not over-isolated: two users with different
ids and the same role see the same workspace memory. The boundary is the
workspace, the campaign, and the role — never the user id.

## Roles

| Operation                              | admin | creator | reviewer |
| -------------------------------------- | ----- | ------- | -------- |
| read approved brand facts              | yes   | yes     | yes      |
| read a campaign in an authorised space | yes   | yes     | yes      |
| create a campaign                      | yes   | yes     | no       |
| record approved brand facts            | yes   | no      | no       |
| generate or regenerate a draft         | yes   | yes     | no       |
| edit a submitted draft                 | yes   | no      | yes      |
| approve or reject an asset             | yes   | no      | yes      |
| withdraw a submission                  | yes   | yes     | yes      |
| manage membership and roles            | yes   | no      | no       |
| reach another workspace                | no    | no      | no       |

The matrix lives in `marketing_memory/service.py` as `PERMISSION` and is checked
before any state change. A refusal happens before any memory is read or written.
Hiding a button is not a control, and neither is a route the frontend does not
link to.

## What generated content may not do

Generated text never becomes authoritative memory on its own. `generate_draft`
reads approved memory and writes an *asset*; it does not write memory. The paths
back to authoritative memory are explicit and recorded:

```
approved brand and product knowledge
            +
approved campaign brief
            ↓
      evidence-backed context      (read from two corpora)
            ↓
       content generation          (simulated here)
            ↓
         draft asset               (application state, not memory)
            ↓
   review / edit / regenerate      (each edit is a new revision)
            ↓
        explicit approval
            ↓
      approved final asset
```

A rejected draft is never written to memory at all. A claim with no approved
source stays unapproved: the generator reports what it could not support instead
of inventing a source for it.

Every decision is written to `marketing_app.decisions` with the actor, the
action, the revision and the time, and each asset records the brand and campaign
memory versions it was generated from. That is what lets a later auditor answer
"why was this approved" from records rather than from recollection.

It is worth being precise about what this does **not** establish. A recorded
approval says a reviewer accepted an asset; it does not say the underlying
product claim was true, and it does not say the generator followed its context
faithfully. Provenance and authority in the archive are separate from model
behaviour, and only the first two are Mind Palace's to guarantee.

## Files

| File                       | What it holds                                                     |
| -------------------------- | ----------------------------------------------------------------- |
| `marketing_memory/tenancy.py`  | workspaces, memberships, roles, campaigns, and the corpus mapping |
| `marketing_memory/service.py`  | the workflow and the permission matrix                            |
| `marketing_memory/generator.py`| the simulated generator                                           |
| `marketing_memory/memory.py`   | the HTTP memory client — the only place Mind Palace is reached    |
| `marketing_memory/provision.py`| onboarding: schema, corpora, credentials, seed facts               |
| `demo.py`                      | the runnable walkthrough                                          |

Two databases, kept apart on purpose. This application owns `users`,
`memberships`, `campaigns`, `assets` and `decisions` in its own
`marketing_app` schema. Mind Palace owns the memory, its provenance and its
versions. The application records its own approval in its own tables and never
writes into Mind Palace's — an approval is a fact about a workflow, not about a
memory.

The tests live in `tests/test_marketing_reference_integration.py` so that CI runs
them; `tests/test_tenant_isolation.py` covers the boundary itself against the
same two organisations.