# Integration guide: Mind Palace in a content-generation app

Wire Mind Palace into a FastAPI app using only this document. Every `python`
block below runs in CI against a live server (`tests/test_integration_guide_examples.py`);
no block is illustrative prose.

Conventions used throughout:

- `MIND_PALACE_URL` is the server base URL (for example `http://mindpalace.internal:8000`)
- `BRAND_MEMORY_KEY` is the server-side bearer credential for the brand in question
- All `python` blocks run in one shared namespace, in file order, so later blocks
  may use names defined earlier.

Shell blocks are not run; each shell command must appear verbatim in
`.github/workflows/ci.yml` or the README quickstart (the test checks this).

---

## 1. Running the service next to an app

Start PostgreSQL with pgvector, apply the schema, provision the least-privileged
runtime role, rotate the scope secret, and turn enforcement on. The exact commands
below are taken from the README quickstart and `docs/deployment.md`; they are
not invented here.

```bash
docker compose up -d postgresql
```

```bash
python -m alembic -c migrations/alembic.ini upgrade head
```

Provision the runtime role (idempotent):

```bash
psql "$ADMIN_DATABASE_URL" -v runtime_password="$PW" -f scripts/db/runtime_roles.sql
```

Rotate the scope secret and enable enforcement as the owner role. The secret
and the enforcement flag are a pair: the server computes HMAC tokens from
`MIND_PALACE_SCOPE_SECRET`, and the database verifies them against
`memory_server_secrets.scope_hmac_secret`. They must change together, or every
tenant-scoped query returns zero rows.

```bash
mindpalace secret rotate --database-url "$ADMIN_DATABASE_URL" --out scope-secret.txt
```

```bash
psql "$ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 -c "SET ROLE mindpalace_owner; SELECT mp_set_scope_token_enforcement(true);"
```

```bash
MIND_PALACE_SCOPE_SECRET="$(cat scope-secret.txt)" MIND_PALACE_ENFORCE_SCOPE_TOKEN=on MIND_PALACE_AUTH_MODE=required MIND_PALACE_DEPLOYMENT=server MIND_PALACE_INGEST_ROOTS=/srv/brand-docs uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**Rotation order and the brief outage window.** Update the database row first,
then roll the env var, expecting a brief outage. The safe order is: rotating the
DB value without updating the environment makes every token fail verification,
so requests return 0 rows until both sides agree. Readiness (`/health/ready`)
reports `not_ready` while they differ.

Point your app at the server:

```python
import os

MIND_PALACE_URL = os.environ["MIND_PALACE_URL"]
BRAND_MEMORY_KEY = os.environ["BRAND_MEMORY_KEY"]
client_url = MIND_PALACE_URL
```

---

## 2. Credentials: one per brand, server-side only

Issue one read_write credential per brand. Credentials are bearer secrets: they
grant access to exactly the corpora listed at issue time, and nothing else.

```python
# The credential is read from the environment. It is never sent to a browser
# and never appears in a response body.
print("credential length:", len(BRAND_MEMORY_KEY))
```

Create a credential for a new brand with the operator command (the maintenance
role, not the app):

```bash
mindpalace corpus create trailhead
mindpalace credential issue trailhead-backend --corpus trailhead --role read_write
```

Hold the secret server-side, in the same environment that serves the app. A
credential that reaches a browser is a credential that can read that brand's
memory.

---

## 3. Layout: one corpus per brand, folders per campaign

**One corpus per brand.** A corpus is the tenant isolation boundary: a credential
for corpus `trailhead` cannot read corpus `globex`, regardless of what path you
ask for.

**Brand-wide documents live under `brand/`.** Product specs, voice guides,
do-not-use rules — anything every campaign draws on.

**One folder per campaign under `campaigns/<id>/`.** Launch dates, offer facts,
campaign headlines. Siblings never see each other's facts.

**Remembered decisions use `scope`:** the stored frame the decision belongs to
(for example `campaigns/spring`). With scope, the stored claim key is
`"<scope>/<key>"`, so the same key in two scopes is two documents that never
supersede or conflict.

```python
# Ingest the brand kit and both campaign briefs through the REST ingest route.
import httpx

def ingest(content: str, path: str):
    resp = httpx.post(
        f"{client_url}/api/ingest/file",
        json={"content": content, "path": path, "corpus": os.environ["BRAND_CORPUS"]},
        headers={"Authorization": f"Bearer {BRAND_MEMORY_KEY}"},
    )
    resp.raise_for_status()
    return resp

brand_guide = (
    "---\nstatus: approved\n---\n\n# Trailhead brand guide\n\n## Voice\n\n"
    "- Our voice is warm, plain and confident.\n\n## Do not use\n\n"
    '- Never use the word "curated" in copy.\n- Never promise free shipping in ads.\n\n'
    "## Brainstorming\n\n- Maybe try a playful pirate theme someday.\n"
)
brand_specs = "# Trailhead product specs\n\n- The Trailhead 2 bike weighs 9.8 kilograms.\n"
spring_brief = "# Campaign brief\n\n## Facts\n\n- The spring launch date is 14 March 2027.\n"
autumn_brief = "# Campaign brief\n\n## Facts\n\n- The autumn launch date is 9 September 2027.\n"

ingest(brand_guide, "brand/guide.md")
ingest(brand_specs, "brand/specs.md")
ingest(spring_brief, "campaigns/spring/brief.md")
ingest(autumn_brief, "campaigns/autumn/brief.md")
```

---

## 4. The five calls an app needs

### Ingest documents

`POST /api/ingest/file` archives a Markdown document into the corpus. Claims
come from the document's headings, list items and `Key: Value` lines; each is
quoted back from the file byte for byte.

```python
resp = ingest("# Trailhead product specs\n\n- The Trailhead 2 bike weighs 9.8 kilograms.\n", "brand/specs.md")
assert resp.status_code == 200, resp.text
```

### Pack before generation — always with `path_prefix`

`pack(query="", budget=..., path_prefix=["brand", "campaigns/<id>"])` returns
the generation context: the current approved claims in scope, with evidence and
provenance. **Always pass a `path_prefix`** — without it, the pack sees the whole
corpus, including sibling campaigns and unapproved drafts.

```python
from mindpalace_sdk import MindPalace

mp = MindPalace(
    os.environ["BRAND_CORPUS"],
    base_url=client_url,
    api_key=BRAND_MEMORY_KEY,
    timeout=3,
    retries=1,
)

pack = mp.memory.pack(query="", budget=16000, path_prefix=["brand", "campaigns/spring"])
current_claims = [c.claim for c in pack.current_memories]
assert any("warm, plain and confident" in c for c in current_claims), current_claims
assert any("14 March 2027" in c for c in current_claims), current_claims
```

### Remember decisions and corrections

`remember(statement, key=..., scope=..., state=...)` records a decision as
authoritative memory. `scope` files it under the campaign folder. `state` is
the reviewer's verdict: `"approved"`, `"draft"` or `"rejected"`.

```python
r = mp.memory.remember(
    statement="Headline: Ride Into Spring",
    key="spring.headline",
    scope="campaigns/spring",
    state="approved",
)
assert r.event in ("NEW", "UNCHANGED"), r.event
```

A correction supersedes the named claim:

```python
voice_id = next(
    c.id for c in pack.current_memories if "warm, plain and confident" in c.claim
)
r2 = mp.memory.remember(
    statement="Our voice is warm, plain and direct.",
    key="brand.voice",
    scope="brand",
    supersedes_claim_id=voice_id,
)
assert r2.event == "NEW", r2.event
```

### Current for a review screen

`current(query, path_prefix=...)` lists the current memories in scope for the
review UI. With `include_states`, a reviewer can opt non-approved states back in.

```python
cur = mp.memory.current(query="", path_prefix=["brand", "campaigns/spring"])
review_claims = [c.claim for c in cur.current_memories]
assert any("warm, plain and direct" in c for c in review_claims), review_claims
```

### History and evidence for audit

`history(query, path=...)` shows the supersession chain for a document.
`evidence(claim_id)` returns the exact quotes that back a claim.

```python
h = mp.memory.history(query="", path=r2.path)
prev_claims = [c.claim for change in h.changes for c in change.previous]
assert any("warm, plain and confident" in c for c in prev_claims), prev_claims

ev = mp.memory.evidence(voice_id, query="")
assert ev.current_memories or ev.historical_memories, "evidence must name its claim"
```

---

## 5. State: how claims gain and lose authority

Claim state comes from frontmatter `status`/`state`/`tags`, or from heading words;
the most restrictive wins. A document with `status: approved` in frontmatter is
the default. A document under `## Brainstorming` is a `draft` and is withheld
from `pack`/`current` by default.

`include_states` names states the caller wants to see anyway. `withheld_counts`
reports how many were held back, per state.

```python
default_pack = mp.memory.pack(query="", budget=16000, path_prefix=["brand", "campaigns/spring"])
assert (default_pack.withheld_counts or {}).get("draft", 0) >= 1, default_pack.withheld_counts
assert not any("pirate" in c.claim for c in default_pack.current_memories), (
    "draft brainstorming must be withheld"
)

# The withdraw path: a remembered rule is withdrawn by remembering the same key
# and scope with state="rejected".
r_rule = mp.memory.remember(
    statement="Never mention competitors by name.",
    key="brand.rule.competitors",
    scope="brand",
)
assert r_rule.event == "NEW", r_rule.event

r_withdraw = mp.memory.remember(
    statement="Never mention competitors by name.",
    key="brand.rule.competitors",
    scope="brand",
    state="rejected",
)
assert r_withdraw.event == "NEW", r_withdraw.event

p_after = mp.memory.pack(query="", budget=16000, path_prefix=["brand", "campaigns/spring"])
assert not any(
    "Never mention competitors by name" in c.claim for c in p_after.current_memories
), "withdrawn rule must not be current"
assert (p_after.withheld_counts or {}).get("rejected", 0) >= 1, p_after.withheld_counts
```

`include_states` opts rejected claims back in for the audit view:

```python
audit = mp.memory.pack(
    query="", budget=16000, path_prefix=["brand", "campaigns/spring"], include_states=["rejected"]
)
found = next(
    (c for c in audit.current_memories if "Never mention competitors" in c.claim),
    None,
)
assert found is not None, "rejected claim must be visible with include_states"
assert found.info_state == "rejected", found.info_state
```

---

## 6. Failure handling: generation never fails because memory failed

Configure the client with `timeout=3, retries=1` on the generation path. On
`MemoryClientError`, generate without memory, log it, and tell the reviewer
"brand memory unavailable".

```python
from mindpalace_sdk import MemoryClientError

gen_client = MindPalace(
    os.environ["BRAND_CORPUS"],
    base_url=client_url,
    api_key=BRAND_MEMORY_KEY,
    timeout=3,
    retries=1,
)

try:
    gen_pack = gen_client.memory.pack(
        query="", budget=16000, path_prefix=["brand", "campaigns/spring"]
    )
    context_text = "\n".join(c.claim for c in gen_pack.current_memories)
    memory_available = True
except MemoryClientError as exc:
    print(f"brand memory unavailable: {exc.code} ({exc.status_code})")
    context_text = ""
    memory_available = False

assert memory_available, "live server must answer"
assert context_text, "live server must return claims"
```

`remember` retries are safe on the same day: submitting identical arguments
returns `UNCHANGED` rather than a second version, so a retried write never
duplicates.

---

## 7. Budgets: size of the JSON response, not the prompt

The `budget` argument is the size of the **whole JSON response** in characters,
including ids, evidence quotes, and provenance — not the prompt text. One current
claim costs about **1,500 characters** of response JSON (its id, version id, path,
timestamps, and evidence fields), while the claim sentence itself is about 60
characters.

Measured claims-per-budget on a 5-claim brand kit (`path_prefix` scoped pack),
CI run 41902031212:

| Budget | Current claims kept | Changes | Truncated | JSON chars |
|--------|-------------------|---------|-----------|------------|
| 4000 | 2 of 5 | 0 | yes | 3108 |
| 8000 | 5 of 5 | 0 (all dropped) | yes | 7348 |
| 16000 | 5 of 5 | 3 | no | 10821 |

Starting budget: `1,500 × (number of rules and facts the generation needs) + 500`,
never below 8000. For a brand kit of 5 claims, that is 8000; at 8000 the pack
fits all current claims but drops all change entries (hence `truncated: true`).
Use 16000 when you also want the change history in the same call.

**Build the prompt from claim texts only.** The pack's `current_memories` carry
each claim's sentence in `claim`; join those sentences for the prompt. The
prompt stays small (about 60 characters per claim); the budget pays for the
metadata the server attaches, not for what your model reads.

**"4 characters per token"** is a rough approximation for the prompt text only.
It does not apply to the budget: the budget counts the full JSON response
(ids + evidence + provenance), which is about 25× the claim text.

```python
# Budget from the count of claims the generation needs.
def recommended_budget(claims_needed: int) -> int:
    return max(8000, 1500 * claims_needed + 500)

assert recommended_budget(5) == 8000
assert recommended_budget(11) == 17000

# The prompt is built from claim texts only.
pack4 = mp.memory.pack(query="", budget=16000, path_prefix=["brand", "campaigns/spring"])
prompt_text = "\n".join(c.claim for c in pack4.current_memories)
assert prompt_text, "prompt must be built from claim texts"
print(f"prompt chars: {len(prompt_text)}")
```

---

## 8. Measured numbers

From `tests/test_integration_brand_kit_scenario.py`, CI run 41902031212
(200 documents of ~300 words each, 20 `pack` calls at budget 16000 against a
live server on a hosted runner; runner timings vary — an earlier run,
41860425143, measured p50 64.5 ms / p95 79.4 ms):

- p50 = 87.2 ms
- p95 = 97.6 ms
- Ingest of the 200 documents: 31.2 s

`p95 < 1000 ms` is asserted in the test; the measured p95 is an order of
magnitude below the bound.

Runner timings vary by runner.

---

## 9. What it does not do

- **Query and chunk routes do not withhold.** `pack` and `current` withhold
  draft/rejected/superseded claims; `query` does not (#138).
- **Authored `claims:` blocks ignore frontmatter `status`.** A `claims:` block
  in frontmatter is the authoring interface and wins outright; file-level
  `status:` does not downgrade its claims (#140).
- **No token budgets.** `budget` is a character count of the JSON response.
- **No metadata filters.** Scoping is by corpus and `path_prefix`; there is no
  tag or metadata filter parameter.
- **No single-claim deletion.** Withdrawing a rule is a new `rejected` version
  of the same key; the archive is append-only. The prior claim stays in
  `history`.
- **No comparative benchmark result.** No number in this repository is evidence
  of generalisation; held-out v1 is burned and there is no independent
  question set.
