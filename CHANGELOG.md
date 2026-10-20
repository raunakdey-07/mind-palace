# Changelog

All notable public releases are listed here. Milestone identifiers are
preserved inside each release entry and map to the public semantic version
through [`docs/release-map.md`](docs/release-map.md).

## [Unreleased]

> ### Upgrade notes — read before upgrading
>
> **Schema.** `alembic upgrade head` moves `007` to `010`. Three migrations have
> never shipped in a tag; all three are new since `v0.9.0`.
>
> | migration | what it adds | reversibility |
> |---|---|---|
> | `008_tenant_scoped_access` | service credentials and row-level security on the protected tables | `downgrade` refuses while more than one evidence row per claim exists |
> | `009_permission_granularity_and_erasure` | per-claim/per-corpus permissions and corpus erasure | reversible; `docs/security.md` names the tables that keep their rows |
> | `010_bind_unrestricted_scope` | binds the `{*}` tenant sentinel to a role that already reads every row | reversible |
>
> **Server mode needs a grant step.** The `server` profile requires
> `MIND_PALACE_AUTH_MODE=required`, `MIND_PALACE_DEPLOYMENT=server`,
> `MIND_PALACE_INGEST_ROOTS`, and a database role that is **not** a superuser
> and does **not** hold `BYPASSRLS`. `scripts/db/runtime_roles.sql` creates that
> role and is **idempotent** — running it twice is safe and a second run with a
> new password rotates it. Startup refuses, in the lifespan, before the process
> accepts a request, and `/health/ready` reports the same verdict as `503`.
>
> **A downgrade destroys data.** Downgrading to `007` **drops
> `memory_principals`** and **disables row-level security on all 9 protected
> tables**. It **preserves all 6 claims**. Take a backup before you try it. The
> upgrade path itself preserves everything: a `v0.9.0` archive upgraded to `010`
> keeps every claim identity, every receipt still verifies offline, and the
> Memory Pack is byte-identical once the clock is pinned.
>
> **There are no tenants to preserve.** Nothing has been released, so an upgrade
> from `v0.9.0` has no user data behind it. This is the cheapest release this
> project will ever have and it is also the only release where that is true.
>
> **Sealed scoring has never happened.** No number in this repository is
> evidence of generalisation. Held-out v1 is burned, the dev set has been looked
> at, and there is no independent question set.

A security change, and a change to one documented claim. No memory semantics,
temporal behaviour, evidence rule, supersession behaviour, receipt schema or
Memory Pack version changed, and no benchmark input was touched.

### Fixed

- **A statement wrapped over three lines was embedded as three sentences' worth
  of dilution.** The mean-pooled vector of a long statement is the mean of all
  its words, and the continuation clauses are detail, so averaging them in
  pulled the claim's own topic down. The row-level-security claim in
  `docs/security.md` scored 0.530 on "How are tenants isolated in the
  database?" as the line the writer wrapped it in, and 0.465 as the whole
  paragraph, which put it outside the band the response is built from. A claim
  is now embedded as its lead: the line the writer put the topic in, plus the
  heading it sits under. The claim itself is still the whole statement, and
  the answer still reads as the sentence the writer wrote.
- **A four-word log line outranked the sentence that answered the question.**
  "embedding suite: `7 passed`;" shares one word with "How large is the
  default install and which embedding backend does it use?" and scored higher
  on the embedding than the runtime.md claim about the 216 MB install, which
  carries two of the question's five concepts. The order now counts how much
  of the question a claim answers as well as how alike it is, weighted by how
  rare each of the question's words is in the corpus. The acceptance test is
  unchanged: the thresholds are measured on the raw cosine.
- **A question about a subject the corpus has never used was answered from the
  corpus's own vocabulary.** "How does Mind Palace use Kubernetes?" matched
  "Mind" and "Palace" and returned claims about the project's portability. A
  question whose subject words are all absent from the corpus abstains, and so
  does a question whose subject is in the corpus but is not what the corpus's
  best answer is about.
- **A bullet wrapped over three lines was archived as its first line.**
  `docs/runtime.md` says the default install is 216 MB, and the sentence that
  says so runs over three lines. The extractor cut the claim at the first
  newline, so the archive held "The default install is 216 MB and a" and the
  rest of the fact with it. A statement is now read as a whole: a bullet, a
  numbered item and a `Key: Value` line each run to the end of their
  continuation lines, and the claim text and value are that statement with its
  line breaks collapsed to single spaces. The evidence quote is the raw slice,
  line breaks and indentation intact, because evidence has to be verifiable
  against the file byte for byte. The chunker cuts on the same boundaries the
  extractor reads on, so a statement is never split across two chunks, which
  would leave its claim with no quotable evidence at all. A re-sync of a corpus
  ingested before this change re-reads it: the version fingerprint covers the
  extracted claims, not only the file's bytes, so 67 of 78 files were
  re-ingested even though none of them had changed.
- **A response of three claims reported a margin of `score - 0.0`.**
  The shortlist was the top of the whole ranking, filtered to the claims the
  response actually carried. When the two keys above the response's own were
  not in the response, the shortlist was left with one entry, and one entry has
  no runner-up, so the margin came out as the score itself and the answer was
  called clear. The shortlist is now the claims that were returned, in score
  order, and the margin is measured against the runner-up the caller can
  actually see.
- **The flight recorder recorded one claim id beside a response that
  displayed three.** It recorded the ranking's shortlist, which carries one id
  per key and is not the set of claims the caller was shown. A verdict filed
  against the answer was therefore graded against a claim the caller never
  saw. The row now carries the ids of the claims that were returned.
- **A question about a subject the corpus does not have was answered with
  claims about the project's own name.** "How is Kafka configured in Mind
  Palace?" matched on "Mind" and "Palace", which are in a sixth of the
  corpus's claims, and returned three claims about the project's portability.
  The relevance gate now discounts the corpus's own vocabulary and the verbs
  that name an action rather than a subject, and a question whose subject the
  corpus's best answer is not about abstains instead of answering.
- **Every command asked the Hub about a model it already had.** The ONNX
  loader fetched the model file and the tokenizer over HTTPS on every
  invocation, which cost two round trips and brought back the Hub's
  "you are sending unauthenticated requests" notice on stderr. Both files are
  now resolved from the local cache first, and the network is the fallback. A
  warm cache makes no request at all, prints no notice, and takes about half
  the time.
- **A refused database connection bypassed the CLI's failure handling.**
  `MindPalace(...)` connects to the database in its constructor, and
  `ingest-repo` built its client *outside* the `try` block that turns failures
  into guidance. A stopped PostgreSQL therefore produced a 130-line
  `asyncio`/`sqlalchemy`/`asyncpg` stack trace where the operator needed a
  sentence and a command. Every command that opens a client now does it inside
  the handler, and a driver-level connection failure is translated into the
  same `database_unavailable` advice the taxonomy already defines. The
  underlying exception is still reachable — `--verbose` adds its type and
  message, `--debug` (or `MIND_PALACE_DEBUG=1`) adds the full traceback — but
  it is never the only thing shown and never the first.
- **A rejected password was reported as a dead database.** The two look alike
  to a driver and nothing alike to a person: one is fixed by changing a value,
  the other by starting a service. Telling somebody to start the database when
  the database is already running sends them the wrong way, so an
  authentication failure now says which of the two it is.
- **`mindpalace init` could not start a local database that had been stopped.**
  After a reboot, a container in `Exited` state stayed stopped, and the
  operator had to know that `podman ps -a` was the command to run. The CLI now
  looks for a stopped local PostgreSQL container publishing the configured port
  and starts it, waiting for it to answer a query rather than merely accept a
  connection — Postgres accepts TCP before it will serve, and a check that
  stopped at the socket reported a container as ready while it was still
  starting up. `init` goes one step further and provisions a `pgvector`
  container when there is nothing to start, so it is genuinely one command on a
  machine that has never run Mind Palace. A remote database is never touched.
- **`docker-compose.yml` could not be started by `podman-compose`.** The file
  required `${MIND_PALACE_RUNTIME_PASSWORD:?...}`, and `podman-compose`
  evaluates a required variable eagerly across every service — so the `backend`
  service's secret stopped the `postgresql` service from starting at all, even
  though that service has no secret in it. The variables now have local
  defaults, and the strictness that mattered moves to where it belongs: a
  deployment in `server` mode now refuses to start while a secret still holds
  its local-development default, because those defaults are printed in the
  repository. The PostgreSQL service also carries `restart: unless-stopped`, so
  a reboot no longer leaves port 5432 dead.

- **The unrestricted tenant scope was self-assertable** (migration
  `010_bind_unrestricted_scope`). `mindpalace.tenant_scope` is a session setting,
  so any role that could open a connection could write `{*}` — the sentinel for
  "this principal may reach every corpus" — and both the row-level predicate and
  the `SECURITY DEFINER` erasure function took it at face value. The
  least-privileged runtime role could therefore declare itself unrestricted, read
  every tenant's rows, and **erase a corpus it held no grant on**. This was
  demonstrated, not theorised: with a corpus id derived from its name, the corpus,
  its document, claim, evidence row and version were all removed.
  `{*}` is now honoured only by a role that already reads every row unauthenticated
  — a superuser, or one that may use the owner of `corpora` — so the runtime role
  can no longer manufacture it. The check names `session_user`, not
  `current_user`: inside a `SECURITY DEFINER` function `current_user` is the
  function owner, so checking it would have asked "is the definer trusted" and
  answered yes for every caller.
- **`PUBLIC` held `EXECUTE` on every `SECURITY DEFINER` function.** PostgreSQL
  grants it on every new function, and the provisioning script revoked only from
  `mindpalace_runtime`, which removed nothing — any *other* role that could
  connect could still call `mindpalace_erase_corpus`. Revoked from `PUBLIC` and
  granted to the runtime role by name. The row-level predicate keeps its
  `PUBLIC` grant deliberately: a policy is evaluated as the querying user, so
  revoking it would break isolation for every role not named.
- **The container image published an unauthenticated API by default.** The
  `CMD` bound `0.0.0.0` while `MIND_PALACE_AUTH_MODE` defaults to `off`, so a
  bare `docker run` served an unauthenticated API to whatever network it landed
  on. It now binds `127.0.0.1`; publishing it is a deliberate act, and
  `MIND_PALACE_DEPLOYMENT=server` still refuses to start unless authentication is
  on and the role enforces the policies.
- **`scripts/db/runtime_roles.sql` did not parse.** Three `REVOKE EXECUTE ...
  FROM PUBLIC` lines were missing a closing parenthesis. It had never been run
  end to end: the test harness kept a hand-maintained copy of the script, and
  the copy was correct. Every isolation test passed against a role no deployment
  had. The harness now reads the shipped file and runs it, so the two cannot
  drift apart again.
- **Memory content reached the log through PostgreSQL's error detail.** Every
  constraint violation returns the offending row in its `DETAIL`, asyncpg passes
  that string through verbatim, and SQLAlchemy's `hide_parameters` cannot help —
  it hides bound parameters, and this text comes from the server. Any traceback
  printing the exception wrote claims, document names and paths to the log. An
  ASGI middleware now catches unhandled errors, logs the method, path and
  exception *type*, and returns a plain `500 internal_error`; the three
  embedding-cache failure logs dropped `exc_info=True` for the same reason. A
  registered `@app.exception_handler(Exception)` would not have been enough —
  Starlette sends that response and then re-raises, explicitly so the server can
  log the traceback. The cost is real: a 500 no longer prints a traceback, so
  debugging means reproducing it.
- **`/health/ready` published the database's defences.** It returned the
  connected role's name, whether it was a superuser, whether it held `BYPASSRLS`,
  whether it could enumerate credential hashes, and which archive tables it could
  `DELETE` — to anything that could reach the port. It now reports a verdict
  (`row_level_security: enforced`, and `503` when it cannot be verified in
  `server` mode), and the inventory is what `mindpalace posture` prints for an
  operator who holds database credentials.
- **The reference integration imported Mind Palace's internals.** `provision.py`
  imported `api.services.corpora`, `api.services.authz` and `api.services.db` to
  create corpora and issue credentials, and both it and `demo.py` imported
  `api.main.app` and drove it through `httpx.ASGITransport`. The suite's own
  import check carried a written exception naming exactly those four lines —
  which is a test that has already decided what it is willing to miss. Corpus
  creation and credential issuance now go through the documented `mindpalace
  corpus create` and `mindpalace credential issue` commands, the demo connects to
  `MIND_PALACE_URL`, and the suite starts a real Mind Palace on a real socket for
  it to talk to. There is no exception list any more.
- **A Markdown table was invisible to `recall`.** The primary metrics in a
  benchmark table are exactly the numbers a reader asks for, and the extractor
  skipped every line that opened with a pipe. `docs/runtime.md` holds the
  `sentence-transformers` against `onnxruntime` import and recall times; the
  `cv-embed` performance report holds the bundle sizes and the latency
  benchmarks. A table is now read: the header row names the columns, the
  separator under it is what makes it a table, and each data row becomes one
  claim -- the row's label followed by `<Column Header>: <Cell Value>` pairs. The
  evidence is the row exactly as the document wrote it, so it is findable byte
  for byte, and the chunker puts that reading beside the row so the claim's own
  wording is in the chunk the archive quotes it from. A table too long for one
  chunk is split by row and every chunk carries the header.
- **A supersession reference that named nothing was written anyway.**
  `mindpalace remember --supersedes <id>` with an id that no live claim carried
  wrote the new claim and left the old one standing, so the archive held two
  live claims about the same thing and the caller was told nothing. The write
  is refused now, and the refusal names the reference that failed. The same
  reference in a document -- `supersedes: <id>` in frontmatter -- stays a
  warning, because a repository mid-migration is full of references to documents
  that have not been written yet.
- **The id `recall` prints was not an id the archive accepted.** `recall` shows
  twelve characters of a sixty-four character id and tells the caller to use it,
  and a document that named those twelve characters named nothing: the
  reference matched only whole claim keys and whole paths. A hex prefix of
  eight to sixty-four characters now matches the active claims, and more than
  one match is refused rather than resolved by taking the first.
- **`mindpalace remember --file` dropped `--supersedes` on the floor.** The
  file branch of `remember` called the writer without the supersession
  argument, so the write succeeded, printed `Remembered.`, and left the claim it
  was meant to retire standing. The flag is passed through now.
- **The CLI called two claims "almost the same" and printed a fifth apart.**
  The margin was the raw-cosine difference while the candidate scores beside it
  were the coverage-adjusted ones, so the banner said "two claims score almost
  the same" beside 0.755 and 0.556 -- a gap of 0.199. The margin is measured on
  the scale the candidate list shows, and a response of one claim is measured
  against the runner-up in the ranking rather than against `0.0`, which read
  like a decisive win over a field that was never there. When the near-tie is
  what decides the verdict, the competitor is listed with it: a near-tie the
  reader is not shown is a near-tie they cannot adjudicate.
- **A history read that found claims said `abstained`.** The status is the one
  word on the wire that says nothing was found, and a history read that
  returned claims and events left it at its default. It says `confident` now,
  and `abstained` only when the read found nothing.
- **An ingest report counted chunks and never said how many claims the corpus
  ended up with.** A document that produces no claims contributes nothing to
  memory, and a report that cannot say so cannot be read as one. The sync
  summary carries the claim count.
- **Ingesting a repository spent most of its time on padding.** The embedding
  session was already batched, but a batch is padded to its longest member, and
  the batches were the ones the reader happened to produce. Text is now sorted
  by length and batched with its own kind, which is the difference between
  17.5 s and 8.5 s over 1,429 texts from a seven-file repository. The vectors
  are the same either way: the mask zeroes the padding and the pooling divides
  by the unmasked length, and the largest difference measured across the
  strategies was 0.0.

### Changed

- **A bare `mindpalace` prints where to start.** It used to print
  "Missing command." and a usage line — a statement about the person's typing
  rather than about the product, on the run with the least context. It now
  prints the first three commands, in the order a newcomer needs them, and
  points at `mindpalace --help` for the rest. `mindpalace --help` is unchanged.

- **`/health/ready` answers `503` only in `server` mode.** In `local` there is no
  second tenant to isolate from, the documented workflow is a developer
  connecting as their own superuser, and reporting `not_ready` there would break
  the convenient path for a problem that cannot occur — the same reasoning that
  gates every other `server` requirement, including refusing to start.

- **A previously reported claim was wrong and is now corrected.** The previous
  report said a compromised application process was "bounded to the credentials
  that process holds". It was not: the same `{*}` forge gave it every corpus.
  That is now closed for the sentinel. A *named* scope remains forgeable — a
  corpus id is `sha256("corpus:" + name)` — so a compromised process can still
  reach any corpus whose name it can obtain. `docs/security.md` states this, and
  `tests/test_scope_forgery.py` pins it so a change in either direction is
  noticed.
- **`MIND_PALACE_INGEST_ROOTS=none`** disables the two filesystem ingestion
  routes and satisfies the `server` profile's precondition. `server` previously
  could not start at all without a root list, which coupled *starting the
  service* to *having a filesystem ingestion surface* and blocked an API-only
  deployment that never touches those routes. The risk belongs to the routes, not
  to the process. Without either a root list or `none`, it still refuses to start.
- **`/health/ready` answers `503` when row-level security cannot be verified.**
  An unverifiable guarantee is not a guarantee.

### Added

- **Corpus erasure** (migration `009_permission_and_erasure`). `DELETE
  /api/corpora/{name}` used to return `409` whenever the archive referenced the
  corpus, so a corpus that had ever recorded a fact could never be removed. It is
  now one `SECURITY DEFINER` function that re-checks the caller's transaction-local
  tenant scope, takes the same advisory lock every writer takes, and deletes all
  nine archive tables in dependency order plus the live projection and every
  credential's grant. `mindpalace corpus delete` is the local equivalent.
  Authorization is `manage_corpus`, and another tenant's corpus is a `404` so the
  refusal does not confirm it exists. The result reports table names and row
  counts and nothing else: an erasure audit trail that reproduced the content it
  erased would defeat the erasure, so there is no field in which to put one.
- **Permissions finer than read/write.** `corpus_admin` (write, plus erasing the
  corpora it holds grants on) and `principal_admin` (everything, including issuing
  and revoking credentials) join `read_only` and `read_write`. Cumulative and
  deny-by-default. A workspace credential can no longer mint authority, so a
  compromised tenant credential is not a foothold on the next tenant. Creating a
  corpus still requires the unrestricted administrative identity: a corpus row is
  only insertable inside the caller's own tenant scope, and a principal cannot
  hold a grant to something that does not exist yet.
- **A credential lookup the application role cannot enumerate.** Authentication
  resolves through `mindpalace_resolve_credential`, and `SELECT` on
  `memory_principals` and `memory_principal_corpora` is revoked from
  `mindpalace_runtime`. One query used to return every tenant's credential hashes,
  roles and grants, which a memory service has no reason to be able to do. The
  identity and its corpus scope now arrive in one round trip rather than two.
  Administrative commands (`credential issue|list|grant|ungrant|revoke`,
  `corpus create|delete`) run on `MIND_PALACE_ADMIN_DATABASE_URL`.
- **`mindpalace credential grant` / `ungrant`**, so access can be widened or
  narrowed without changing a credential's secret — reissuing breaks every running
  deployment of the caller, so it is only ever the rotation step. Removing the
  last corpus leaves a credential that resolves to nothing and is refused on every
  request, which makes `ungrant` a safe suspension.
- **`mindpalace posture` now checks privilege shape**, not just policy flags: the
  role's privileges beyond DML, whether it can enumerate credential hashes,
  whether it holds `DELETE` on any archive table, and whether it can `SET ROLE`
  into a privileged role. `server` mode refuses to start on any of them, in the
  application lifespan — after the process has its connection, before uvicorn
  reports itself as accepting traffic.
- **Authorization is implemented, and off by default.** `MIND_PALACE_AUTH_MODE=required`
  makes every request present a bearer credential bound to a role and an explicit set
  of corpora. Opaque random secrets, stored only as SHA-256 hashes; rotation is
  issue-then-revoke; revocation takes effect on the next request. `off` remains the
  default because the documented five-minute path is a local single-user workflow.
  `/health/ready` reports the mode, so a deployment cannot be tenant-safe by
  accident.
- **Row-level security on every tenant-owned table** (migration
  `008_tenant_scoped_access`, additive, reversible). The archive was already
  `(corpus_id, ...)`-keyed with composite foreign keys, so this needed no schema
  change: `ENABLE` + `FORCE` + a `USING`/`WITH CHECK` predicate. `chunks` has no
  corpus column, so its policy reaches the corpus through the document it belongs
  to. The predicate fails closed when no tenant scope is present.
- **`scripts/db/runtime_roles.sql`**, which creates the separation the policies
  depend on: a `BYPASSRLS` maintenance role that owns the schema, and a
  `NOSUPERUSER NOBYPASSRLS` runtime role that owns nothing, holds no `DELETE` on
  any archive table, and is granted `EXECUTE` on named functions rather than
  `ALL FUNCTIONS`.
- **A tenant-isolation suite** on real PostgreSQL with that least-privileged role:
  two organisations, three roles each, canary content, every memory operation plus
  search, context, the query surface, the feed, ingestion, corpus administration,
  snapshots, replay, receipts and parent/child id mixing — plus connection reuse,
  concurrency, cancellation and rollback. A negative suite removes each control in
  turn and asserts what it was holding, so the isolation tests cannot pass
  vacuously.
- **`examples/marketing-campaign-memory/`**, a thin reference integration: two
  simulated organisations, brand and per-campaign memory, four platform variants,
  draft/review/edit/approve/reject/regenerate, and a server-enforced role matrix.
  Content generation is a deterministic stand-in, and says so.

### Fixed

- **The entire administrative CLI was broken.** `_authz_session` built a context
  manager from the session helper and then called it again, so every
  `mindpalace corpus` and `mindpalace credential` command raised
  `AsyncContextDecorator.__call__() missing 1 required positional argument` — which
  the CLI's own error handler rendered as "Mind Palace could not complete that".
  No service-level test noticed, because the failure was in the CLI's wiring.
  `tests/test_operator_cli.py` now drives the real entry point end to end.
- **`credential revoke` accepted a name in the Python API but not in the
  database.** The lookup function matched only an id prefix, so an operator who
  typed the credential's own label got "no credential matched" for a credential
  that was right there in `credential list`.
- **An unrecognised `MIND_PALACE_DEPLOYMENT` was silently treated as `server`.**
  That fails closed for authentication but skipped every other server-only check,
  so a typo like `prod` left the ingest-root and database-posture checks
  unenforced while the service ran normally. An unknown profile is now refused.
- **The runtime role held more privilege than the deployment needs.** It could
  `SELECT` every credential hash, `DELETE` from every archive table, `TRUNCATE`
  them, and `EXECUTE` every function in the schema including every
  `SECURITY DEFINER` one. `mindpalace posture` verifies the reduced shape, so a
  role script that drifts from what it claims is a startup failure rather than an
  assumption.
- **The test harness provisioned a different role from the one it tests.** It
  granted blanket DML and `EXECUTE ON ALL FUNCTIONS` on every run, quietly undoing
  `scripts/db/runtime_roles.sql`, so the isolation suite was proving separation
  against a role that could delete the archive and read every tenant's hashes.
  The harness now provisions exactly the documented role.
- **The local runtime socket authorized nothing.** Its token proved the caller was a
  local peer of the same user and decided nothing about which corpora that peer could
  reach, so it dispatched any corpus named on the wire — read or write — in every
  mode, including `required`. It was the default transport for the CLI. It now
  resolves a principal from `MIND_PALACE_RUNTIME_CREDENTIAL` per dispatch and scopes
  every operation with it; without one, `required` mode serves nothing. The socket
  token is compared in constant time.
- **An SDK client built without a corpus name authenticated in no mode** — the exact
  shape the CLI constructs. Its operations ran as the default local principal,
  unrestricted, under `MIND_PALACE_AUTH_MODE=required`. Every local client now
  resolves its identity at construction, so one that cannot authenticate fails there
  rather than partway through a workflow.
- **The SDK took the runtime shortcut outside the caller's scope.** The socket is a
  process boundary, so the scope has to be entered before it is used; otherwise the
  in-process path was authorized and the socket path was not.
- **An `AuthorizationError` escaped the SDK as itself**, so the same refusal arrived
  as one exception class locally and another over HTTP, and `except
  MemoryClientError` did not catch it. Including from the constructor, which runs
  outside the translation an operation gets.
- **`MIND_PALACE_DEPLOYMENT=server` with authentication off started anyway**, which is
  what a bare `uvicorn api.main:app` and the default `docker compose up` produced
  with a published port. The application factory now refuses to start, with the
  reason. `docker-compose.yml` ships `server` + `required` and the runtime role.
- **`scripts/db/runtime_roles.sql` documented a password variable it never read.** It
  created `mindpalace_runtime` as `LOGIN` with no password, so an operator following
  the documentation got a role that could not authenticate — which reads as an RLS
  problem and is not one. The password is now a `psql` variable, quoted as a literal,
  and a missing or empty one fails the run.
- **`corpora.sync` wrote documents, chunks, the manifest and the archive with only a
  read check**, so a `read_only` credential could perform a complete write into a
  corpus it *was* granted. It now requires the read-write role, on both the REST route
  and the SDK.
- **MCP resolved its credential as a bare secret** where an `Authorization` header
  value was required, so every tool answered 401 even when correctly configured. It
  also built its clients without passing that credential on, so `context`, `search`
  and `sync` bypassed the scope the tool had just established.
- **`GET /api/corpora` and the un-named corpus scope enumerated every tenant.**
  Both are now scoped to what the caller may reach, so an omitted corpus can no
  longer mean "the other tenant's" and a listing cannot reveal corpora the caller
  was never granted.
- **`POST /api/ingest/*` wrote into the implicit `default` corpus.** Both routes
  now name their corpus and go through the same authorization choke point, so the
  shared default namespace is no longer an unauthenticated write target.
- **Four repository functions had no corpus predicate at all** — `search_chunks`,
  `get_document`, `get_chunks_for_document`, `get_related_documents`. All four now
  require `corpus_id`; the last one scopes both sides of its tag sub-query, which
  previously read another tenant's tags even when the outer query was scoped.
- **`RelatedService`, `TimelineService` and the benchmark strategy builder called
  retrieval with no corpus**, which `RetrievalService` reads as "every corpus". All
  three now require one, so the global path cannot be reached by omission.
- **`CorpusStatsResponse.last_ingested` was typed `str` and received a
  `timestamptz`**, making `GET /api/corpora/{name}` a 500 for any corpus that had
  ingested anything. The JSON is unchanged; the model now matches the query.
- **Chunk writes and deletes relied entirely on `doc_id` provenance.** They now
  require the corpus and verify the document belongs to it, and stale-document
  deletion names the corpus as well as the id.
- **Revoking an already-revoked credential reported success**, because the row was
  still updated. It now reports what actually changed, and an ambiguous id prefix
  is refused rather than resolved arbitrarily.
- **`deployment.md` still said the API had no authentication.** It does.

### Added

- **`mindpalace credential issue|list|revoke` and `mindpalace corpus create`.** The
  bootstrap was a Python snippet, which meant the first step of every deployment was
  copying code that imports internals. These are local administrative commands on
  purpose: a network route that mints a tenant boundary or a credential is the thing
  the design exists to avoid. `revoke` accepts the id prefix that `list` prints.
- **`mindpalace posture`.** Whether the current database role actually enforces the
  row-level policies, printed and non-zero on a problem. `MIND_PALACE_AUTH_MODE` is an
  application switch and says nothing about the role; a service could report itself
  tenant-scoped while running as a superuser with migration `008` inert. The same
  facts appear in `/health/ready` under `database_posture`, and a check that cannot
  query `pg_roles`/`pg_class` reports the tables as unprotected rather than fine.
- **`MIND_PALACE_DEPLOYMENT=local|server`** as an explicit setting rather than a
  heuristic. `server` requires `required`, a set of ingest roots, and a database
  role that actually enforces the policies — checked in the application lifespan,
  before the process accepts a request. An unrecognised value is refused.
- **`tests/test_operator_cli.py`**, which drives `mindpalace corpus` and
  `mindpalace credential` through their real entry point. The service functions
  they call were all tested; the wiring around them was not, and it broke.

### Changed

- **The reference integration no longer imports Mind Palace internals.** It called
  `api.services.memory_public.execute` in-process and described that as public. It was
  not: an adopter needing those imports could not be pointed at a different
  deployment. It now speaks the public REST contract over `httpx`, through the real
  middleware and the real policies, and a test fails if any file under the example
  imports one. The one exception is onboarding, where creating a corpus and issuing a
  credential are administrative and have no HTTP route by design.
- **A corpus name is no longer described as a tenant boundary.** It is a namespace;
  whether a caller may address one is decided by its credential. `README.md`,
  `deployment.md` and `docs/STATUS.md` are corrected, and `docs/security.md`
  documents the threat model, the deployment modes, the database roles, the five
  access surfaces, the tenant scope and the known limitations.

### Compatibility

- **New migration, `008`.** Additive: two new tables, new policies on existing
  tables. `downgrade` removes them. Existing data is not reclassified and no row is
  assigned an invented owner — every pre-existing corpus keeps whatever scope it
  had, which under `off` is unrestricted and under `required` is empty until a
  credential is granted.
- `MemoryResponse` gained no field, so the version 1 JSON contract is unchanged.
  The MCP tool set is unchanged: authentication is inherited from the same choke
  point through an environment variable, not added as a tool.
- `DELETE /api/corpora/{name}` returns a report instead of `204`, because the
  caller needs to know what was removed and, more importantly, what was not. An
  empty `204` would have implied that exported receipts and database backups were
  covered too. The status code for a successful deletion is now `200`.
- Administrative CLI commands connect with `MIND_PALACE_ADMIN_DATABASE_URL` when it
  is set, and fall back to `DATABASE_URL` otherwise, which is what keeps the
  single-user local workflow working with one setting.
- `/health/ready` gained a `database_posture` object. Additive; the existing keys
  are unchanged.
- A deployment that leaves `MIND_PALACE_AUTH_MODE` unset behaves exactly as before.
  `MIND_PALACE_DEPLOYMENT` defaults to `local`, so no existing deployment is
  refused a startup it used to get. A deployment that uses a superuser or
  `BYPASSRLS` database role gets no tenant isolation from the database layer
  whatever the mode says; `mindpalace posture` and `/health/ready` now say so,
  and it is stated in `docs/security.md` rather than left to be discovered.
- `scripts/db/runtime_roles.sql` now requires `-v runtime_password=<password>` (or
  `-v runtime_password_skip=on`). Running it without either fails with the reason
  instead of creating a role that cannot log in.

## [v0.9.1] - 2026-10-08

A patch on `v0.9.0`. No memory semantics, retrieval behaviour, public response field
or benchmark input changed; `M006.75`/`M006.5`/`M013` results are untouched and the
disputed 47/60 artifact was not regenerated.

The point of this release is that the previous release's claims are now checkable from
a clean machine.

### Fixed

- **The test suite could not pass on a clean runner.** CI installed
  `sentence-transformers` but never the weights and ran pytest with
  `HF_HUB_OFFLINE=1`, so a clean runner had the package and not the model. 41 tests
  failed on `OSError: We couldn't connect to ...` -- everything that indexes or ranks
  anything -- and `--maxfail=1` reported only the first. A developer machine already
  had the cache, which is what hid it.
- **The CI badge rendered as `404: badge not found`.** The URL was a release-badge
  path with a workflow path appended; shields.io answers that with HTTP 200 and an
  error SVG, so it looked like a mangled image rather than a dead link. Verified by
  rendering every badge URL and reading the aria-label back.
- **The wheel shipped the research tooling.** `scripts/` had been in the distribution
  since `v0.6.0`: 54 files, 590 KB of 1,328 KB unpacked, 44% of the distribution, none
  of it imported at runtime. The wheel is now **227 KB / 74 files, down from 425 KB /
  129**. The repository still ships those scripts; they are where `check_versioning.py`,
  `check_evidence.py` and the benchmark harnesses run from.
- **The wheel CI job asserted something no install could satisfy.** It ran
  `mindpalace-mcp --help` against a *bare* `pip install dist/*.whl`, but `mcp` is a
  declared extra, so the import raised `ModuleNotFoundError: No module named 'mcp'`.
  The step's `|| python -c "import mcp_server"` fallback could not have rescued it
  either — same missing package. MCP is now verified in its own step from an install
  that includes the extra, and the bare install's *omission* of `mcp` is asserted as
  the design it is.

  `mindpalace-mcp --help` is not run there either, for a second reason worth writing
  down: `main()` takes no arguments and goes straight to `mcp.run_stdio_async()`, so
  the process would block on stdin until the job timed out. The old step never reached
  that line, because the import failed first.

### Evaluated, not adopted

- **Python 3.15 lazy imports.** Measured on CPython 3.15.0rc1. The semantic cold path
  cannot improve: the model stack costs 5.61 s to import against a 0.11 s CLI import
  graph, and `sentence_transformers` is *already* imported lazily inside
  `Embedder._get_model()`, so there is nothing to defer on the path that matters.
  A narrow `__lazy_modules__` prototype changed nothing measurable, and
  `-X lazy_imports=all` breaks real commands with a partial SQLAlchemy initialisation
  (`AttributeError: type object 'array' has no attribute 'ARRAY'`). Full table and
  reasoning in [the release map](docs/release-map.md#python-315-lazy-imports-were-evaluated-and-not-adopted).
- **The model stack as an optional install extra.** Not done, deliberately: making
  `sentence-transformers` optional would mean a plain `pip install mindpalace-os`
  stopped ranking semantically, which is a silent behaviour change for the majority
  case. What *is* guaranteed, and tested, is that nothing needs the model merely to
  import or run a command -- `tests/test_m016_model_free_package.py` blocks the stack
  at the import hook and checks `--help`, `init`, `recall` and `explain` still work.

### Measured, not changed

A read whose claim vectors are all cached still costs ~6.6 s, because
`memory_query.py:267` asks the embedder for its dimension and that loads the model.
`memory_claim_embeddings.embedding_dimension` already records it. Recorded rather than
changed: the read path is authoritative, and a wrong dimension would silently corrupt
ranking. It is a future-milestone candidate, not a patch.

### Compatibility

- No migration; the schema head does not move. No public response field changed.
- Python `>=3.11` remains the floor. 3.15.0rc1 runs the model-free path correctly,
  including the announced degradation to lexical, but torch publishes cp310-cp314
  wheels only, so the semantic path cannot run on 3.15 yet and was not measured there.
- Memory Pack stays at version 1. The receipt schema stays at version 1.

## [v0.9.0] - 2026-10-08

Milestones `M015.2` (five-minute memory path) and `M016` (instant recall, explicit
model-free mode, real-project dogfooding), consolidated into one public release. They
are one milestone for a product: `v0.9.0` is the release in which Mind Palace becomes
something a developer can install, use in five minutes, and keep.

Verification still establishes integrity and recorded provenance. It does **not**
establish that the original source was factually correct, and it does not establish
authenticity unless a digest pinned out of band is supplied.

### Five-minute memory workflow

```console
$ mindpalace init
$ mindpalace remember "The production datastore changed from SQLite to PostgreSQL." --key datastore
Remembered.
  version: 5aae989d3134 (new)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md

$ mindpalace recall "What datastore does production use?"
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC
```

Seven commands — `init`, `remember`, `recall`, `explain`, `history`, `receipt`,
`verify` — that take a statement and a question.

- **The default corpus.** No `--corpus` on the first command. Scoping stays available
  for anyone who needs it.
- **Idempotent writes.** The same statement twice returns the same `version_id` and
  `changed=false`. Four concurrent identical writes produce exactly one version and
  one claim, so a retry after a lost response converges instead of duplicating.
- **A statement becomes authoritative memory**, with exact-substring evidence, through
  the same archive a synced document uses. Not a search index entry.
- **Concise recall.** Answer, source, and the time it was true. Depth is on request.
- **Explicit abstention.** `NO_RELEVANT_MEMORY`, never a guess, and never evidence
  cited for an abstention.
- **Evidence-aware `explain`**: the exact characters that support the answer, its
  supersession history, and whether a receipt is available to export.
- **Writes need no embedding model.** `remember` never imports the model stack.

### Instant repeated reads

A local runtime keeps the embedding model and the database pool warm, and the CLI uses
it without the user choosing to.

| command | before | after |
|---|---|---|
| `mindpalace recall` | 6.17 s | **0.63 s** |
| `mindpalace explain` | 6.23 s | **0.67 s** |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| `mindpalace history` | 0.81 s | 0.61 s |
| `mindpalace verify` | 0.56 s | 0.56 s |
| SDK `recall`, fresh process | 5.11 s | **0.51 s** (opt-in `runtime=True`) |

MCP is unchanged, and correctly so: an MCP server is a long-lived process and pays the
import once.

The cause was measured with `-X importtime` rather than assumed. A one-shot recall was
6.53 s, of which 5.32 s was `import sentence_transformers` (1.68 s `torch`, 1.20 s
`transformers`, 2.66 s the rest), 0.49 s was model construction, and **61 ms was the
memory work**.

- `mindpalace runtime start | stop | status`, including `--json`. Optional: every
  command works with no runtime at all, and `MIND_PALACE_RUNTIME=0` turns it off
  everywhere.
- **Crash and stale-socket recovery.** A killed runtime leaves a socket file; the next
  command removes it and starts a fresh one. SIGTERM finishes any in-flight request,
  unlinks the socket and exits 0. Idle 15 minutes, it exits on its own.
- **Unix socket only.** A 0700 directory, a 0600 socket, and a token read from a 0600
  file — never from a command line, where any local user could read it out of `ps`.
  No code path binds a TCP port, and there is a test that asserts it.
- **Protocol versioning.** A runtime refuses a request that asks for a different
  protocol, with an instruction to restart it, rather than answering with the
  semantics of the build it was started from.
- **No model, no silent different answer.** If the model cannot load, the runtime
  records the reason and the read takes the same path it would have taken without a
  runtime.

### Verifiable memory

Carried forward unchanged from v0.8.0 and now reachable from every surface.

- Receipts on REST, SDK, MCP and the CLI; `mindpalace receipt` writes a self-contained
  file.
- **Offline verification.** `mindpalace verify` needs no server, no database, no model
  and no network. So does `mindpalace-proof`, on a bare `pip install mindpalace-os`
  with no CLI extra, because the receipt carries the response it describes.
- **Cross-surface parity.** CLI, SDK, REST and MCP return the same authoritative
  memory, evidence, temporal state and receipt. The runtime path does too, asserted in
  `tests/test_m016_runtime_equivalence.py`.
- **Memory Pack v1** unchanged, and byte-identical whether produced in-process or
  through the runtime.

### Explicit model-free mode

`MIND_PALACE_LEXICAL=1` reads through the lexical relevance rung that already existed
as the no-model degradation, taken on purpose.

| | semantic | `MIND_PALACE_LEXICAL=1` |
|---|---|---|
| `recall`, cold process | 6.17 s | **0.63 s** |
| `explain`, cold process | 6.23 s | **0.67 s** |
| model stack imported | yes | **none** |

It is documented as a deliberate trade-off, not a substitute. On a ten-fact corpus
across sixteen question shapes the two modes agreed on fifteen; the difference was an
abstention that semantic ranking did not make. Nothing was weakened to make them
agree.

**A lexical fast path.** A more attractive design was measured and *not* shipped:
find candidates lexically, then load the semantic model only when that looks
insufficient. It would have removed the 5.3 s import from every command rather than
from all but the first, and on 15 of 16 questions it was indistinguishable from the
semantic path. The sixteenth is the abstention above, and shipping it
would have made `NO_RELEVANT_MEMORY` depend on whether the model happened to be
cached on the machine — the exact acceptance coupling that would make an abstention
unreproducible across two developers' laptops.

An explicit mode is honest about the trade-off; an automatic one hides it. Details in
[deployment.md](docs/runtime.md#model-free-lexical-mode).

### Developer experience

- **A public-only example.** `examples/project-log/` uses this repository's own
  decision history as memory. It imports `mindpalace_sdk` and nothing else: no
  `api.services`, no `api.db`, no internal retrieval module, no SQL. If it needed a
  change to work, the SDK would not be usable yet.
- **Errors answer what happened, why, and what to do.** Two were leaking internals and
  are fixed: a bad `--as-of` produced a raw pydantic message, and an unreadable receipt
  file did not say what a receipt is or how to write one.
- **Deterministic JSON.** `recall`, `explain` and `history` return the public
  response contract, verified key-for-key against the SDK. `explain --json` now emits
  the same `{response, receipt}` bundle `mindpalace receipt` writes, instead of
  silently dropping the receipt the human-readable form shows.
- **One failure, one exception class.** The in-process SDK path raised the service's
  own `MemoryError` while the runtime and HTTP paths raised `MemoryClientError`, so
  which one a caller needed depended on whether a runtime happened to be running. All
  three now raise `MemoryClientError` with the same code, message and status. A genuine
  defect is still not disguised as an API failure.
- **`mindpalace runtime status`** when a command feels slow, without dumping internals.

### Fixed

Defects found by review and dogfooding in this release, each now covered by a
regression test:

- **The test suite could not pass on a clean runner.** CI installed
  `sentence-transformers` but never the weights, and ran with `HF_HUB_OFFLINE=1`, so
  **41** tests failed on `OSError: We couldn't connect to ...` — everything that
  indexes or ranks anything, including 11 fixture errors in `test_search_semantics.py`.
  `--maxfail=1` reported only the first, which is how a green local run and a red CI
  run coexisted through a release; a developer machine already had the cache, which is
  what hid it.
- **Both models are now provisioned at pinned revisions**, through
  `scripts/ci/provision_models.py`, and verified to load through the application's
  own `Embedder` and `Reranker` with the network disabled *before* any test runs — so a
  missing model costs one clear line instead of 41 failures forty minutes later.
  The embedder's pin is the revision recorded in the frozen M006.75 artifact, so CI
  verifies the suite against the weights that result was measured with. The script
  also declares `refs/main`, which is what `model_fingerprint` reads to identify the
  model; without that, pinning would have broken the benchmark's own model check.
  `actions/cache` makes a repeat run ~1 s instead of ~60 s, keyed on OS, Python
  version and both revisions, and is an accelerator only — a miss re-downloads and a
  wrong or partial cache is refused. `--maxfail=1` is gone; it was hiding failures,
  not preventing them.
- **A database-outage test was passing for the wrong reason.**
  `test_search_backend_unavailable_is_503_not_empty` asserted only that the word
  "unavailable" appeared in the error detail — and `"Semantic model unavailable"`
  contains it. On a runner with no model the mocked outage was never reached
  (`RetrievalService.search` was never called) and the 503 came from the model
  instead, so the DB-outage contract was not actually tested anywhere. It now asserts
  the exact detail and that the search was reached.
- **Nothing stopped the provisioning step from being deleted again.**
  `tests/test_ci_model_provisioning.py` now reads the workflow and fails if the
  provisioning is missing, weakened, unpinned, ordered after verification, given the
  wrong offline flags, cached without its revisions in the key, or duplicated into one
  job but not the other.
- **A zero-result search test depended on a developer's model cache.** It patched
  `RetrievalService.search` but not the embedder that `/api/search` calls first, so it
  passed locally and failed on CI. Both tests now state their precondition explicitly.
  No production behaviour changed: `/api/search` embeds the query before searching,
  `RetrievalService.search` needs that vector in its SQL, and `503` for an unavailable
  model remains correct — it is a vector endpoint with no lexical rung.
- **The CI step for the model-free mode could not tell a working mode from a silent
  one.** It asserted the exit code, which an abstention also satisfies. It now asserts
  that a discriminating question is actually answered, still imports no part of the
  model stack, and still announces itself exactly. Finding this surfaced a real
  limitation, now documented: lexical ranking scores the terms that discriminate
  between candidates, so a question whose only shared term is one *every* candidate
  carries — `What datastore does production use?`, with two datastore memories — is
  correctly abstained on. Predates v0.9.0 and unchanged by it.

- **A multi-line statement, and `remember --file` on any Markdown file, could not be
  recorded at all.** The frontmatter quote collapsed line breaks while the document body
  kept them, so the evidence the archive was told to cite was never an exact substring
  of the archived chunk.
- A statement beginning with `#` was chunked as a heading and rejected for the same
  reason.
- `POST /api/memory/remember` accepted a `file` path. On an endpoint with no
  authentication, that let any caller read a file relative to the server's working
  directory and recall its contents. The field is gone from the HTTP contract;
  `mindpalace remember --file` and the SDK's `file=` still read local files, which is a
  decision for a trusted operator.
- The REST response computed the claim key and document path independently of the write,
  so a whitespace-padded statement was reported under a key the archive did not hold —
  and a client retrying with that key would have written a second memory instead of
  recognising the first.
- An imported file's absolute path became part of its memory identity.
- `explain` attached the change set to the response *after* its receipt digest was
  computed, so the digest it printed no longer described what it printed.
- Two different keys that reduced to the same filename shared one document, so an
  unrelated fact could supersede another.
- `mindpalace verify` rejected a bare receipt or proof that `mindpalace-proof verify`
  accepts. One verification contract now.
- `mindpalace_runtime` was not packaged, so on an installed wheel the runtime silently
  never started and every command stayed 6 seconds.
- A read discarded the claim vectors it had just computed. A corpus authored without
  embeddings was re-encoded on every read; it is now encoded once.
- A runtime that loaded slowly was reported as `loading` by `start --wait`, which read
  that as failure.
- **The CLI's `explain --json` bundle shape was claimed and unasserted.** Nothing held
  that `explain --json` emits the same `{response, receipt}` document
  `mindpalace receipt` writes, while `recall --json` and `history --json` return the
  bare response. Now pinned, including that the receipt is verifiable against the
  response it ships with and that a rewritten answer is rejected.
- `eval/m00675/README.md` described the stored artifact's 47/60 as the *current*-source
  result. It is not: the current source measures 45/60, and had already drifted before
  v0.9.0. The README now separates the recorded artifact from the reproducible result.
- The README claimed a missing model "never removes the answer", which is true of
  every memory surface and false of the legacy `/api/search`, which has no lexical
  rung and answers 503. The exception is now stated.

### Changed

- `memory_query` now writes back the claim vectors it had to embed. This overturns a
  documented "a query never writes" decision, so it is bounded rather than assumed safe:
  its own `SAVEPOINT`, skipped on a read-only session, best-effort, and a partial batch
  is discarded when the lexical fallback runs. The cache holds no authority, so every one
  of those boundaries costs latency and nothing else.
- **The relevance acceptance gate tests overlap against full claim terms** rather than
  terms reduced by the set shared with every candidate. Shared terms cannot discriminate
  between candidates, so ranking still uses the reduced set; but they are not evidence
  that a question is off-topic, and using them for acceptance made any question about a
  subject unanswerable once a second claim shared that subject. This is the only
  retrieval change since M013.
- A read that fell back to lexical because no model loaded now says so on stderr, once.
  The degradation is correct and long-standing; what was missing was that it was silent.

### Measured

`scripts/five_minute_probe.py`, 200 remembered statements, on mains power, around the
service call: `remember` p50 10.8 ms, `recall` p50 92.6 ms, `explain` p50 93.8 ms,
`verify` p50 0.82 ms in a separate interpreter with no database URL and no network.
At 1000 statements `remember` and `verify` stay flat and `recall` reaches p50 988 ms,
because the authoritative projection loads the version graph before relevance narrows
anything.

Full numbers, method and limits:
[docs/performance/five-minute-path.md](docs/performance/five-minute-path.md). Every
latency figure in this repository's history before v0.9.0 was taken on battery, where
the same probe is 3 to 4× slower; compare within a condition, not across them.

### Compatibility

- **Memory semantics are unchanged.** Claim identities, evidence, provenance, temporal
  validity, supersession, conflicts and abstention are byte-identical between the
  runtime and the in-process path across ordinary, weakly related, absent, temporal,
  conflict and multi-topic queries.
- Memory Pack stays at version 1. The receipt schema stays at version 1. No migration,
  so existing v0.8.0 receipts remain verifiable and the frozen benchmark is untouched.
- `MemoryResponse` and every existing route, SDK method and MCP tool are unchanged.
  `memory_query` is kept alongside `memory_recall`; both reach the same service.
- The local runtime is never required: `MIND_PALACE_RUNTIME=0`, `MindPalace(runtime=…)`
  defaults to off, and a platform without `AF_UNIX` runs in-process.
- `sentence-transformers` remains a declared dependency, so a plain install keeps
  ranking semantically by default. Nothing in the package needs it merely to import or
  to run a command, which is asserted by blocking the model stack at the import hook.

### Frozen benchmark, unchanged

No benchmark question, policy, evaluator semantic, tokenizer or retrieval threshold
was touched by this release. That is now **measured, not asserted**: re-running the
frozen held-out suite under both source trees produces **60 of 60 identical canonical
per-question decisions** between `v0.8.0` and `v0.9.0`, with 0 execution failures and
all seven safety invariants passing in both.

- **M006.75 frozen held-out benchmark** — 60 scenarios, **45/60 (75.0%)** exact and
  55/60 abstention-exact on the current source, 0 execution failures, and **all seven
  safety invariants at 60/60** (provenance, temporal scope, status, conflict closure,
  current-authority preservation, truncation, budget containment).
- **M006.5 added-question set** — 40 questions, **30/40 (75%)** exact, 0 safety
  failures across 264 output cases.
- **M013 held-out v1** — 157 independently authored questions, **131/157 (83.4%)**.

The safety invariants are at 1.000; retrieval accuracy is not, and this release does
not conflate the two.

**One discrepancy is reported rather than fixed.** The frozen artifact
`eval/m00675/result.json` records 47/60, which the current source no longer
reproduces — and did not already at `v0.8.0`. The drift is four questions, all of the
same shape: the expected subject is still recalled and extra keys are admitted beside
it. The artifact's own `--verify` source check fails on `v0.8.0` too, and its recorded
source fingerprint matches no commit. Its **frozen inputs verify unchanged** (dataset
and policy hashes), which is the check that protects benchmark semantics. The artifact
was deliberately **not** regenerated: a re-run is a new research result, and
publishing one under the frozen `M006.75` name would misreport research as a product
gate. Full measurement and the likely cause are in
[the reproducibility report](docs/evaluation/m00675-reproducibility.md).

The 131/157 figure remains a generalisation result and a research limitation, and is
not superseded by the frozen benchmark: they were authored for different purposes and
answer different questions.

### Known limitations

- Recall grows linearly past a few hundred statements; the authoritative projection loads
  the version graph. That work is the guarantee, not overhead to be trimmed.
- The first command in a fresh process pays a ~5.3 s model import unless a runtime is
  already warm, or `MIND_PALACE_LEXICAL=1` is set.
- Authentication is a documented contract, not implemented code.
- Receipts are not signed; `--trusted-digest` covers authenticity against a value the
  user pinned themselves.
- Lexical mode selects different keys from semantic mode on vocabulary-poor questions,
  by design.

## [v0.8.0] - 2026-10-05

### Verifiable Memory

- **Portable Memory Receipts.** A receipt records what Mind Palace actually
  returned to one application, for one query, at one historical state: the claim
  identity and version, the document version and path that carried it, its
  evidence with offsets, its validity window, its supersession lineage, the
  authoritative digest and an embedded proof. `memory_receipt.py` is
  standard-library-only and ships in the distribution.
- **Receipts through every public surface.** `include_receipt=true` on
  `POST /api/memory/query` (and the SDK's `query(...)`) attaches the same
  canonical receipt to the existing response. Clients that do not ask for it see
  no new field at all: `receipt` is absent rather than null. The MCP
  `memory_explain` tool is `memory_query` with the receipt forced on, so an agent
  can ask why a memory is trustworthy without knowing the flag. All three
  surfaces call the same service and return the same object.
- **Historical memory.** `as_of` reconstructs the archive as it was, so a
  question asked before a supersession is answered with the claim that was
  authoritative then, and its receipt says which instant it describes. A claim
  that was `CURRENT` now but not yet valid at the queried instant is reported as
  a note rather than silently accepted.
- **Offline verification.** `mindpalace-proof` gained `receipt` alongside
  `verify`, `prove` and `explain`. Verification needs no server, database,
  embedding model, network or credentials, and `verify --json` reports integrity,
  provenance, temporal and supersession separately, never collapsed into one
  verdict.

### Trust boundary

Verification establishes **integrity and recorded provenance**: that the artifact
still represents the state the receipt describes, and that no covered record has
been altered since. It does **not** establish that the original source was factually correct,
and a digest alone does not establish **authenticity** — a party able to replace
both the artifact and the receipt can recompute the digests. Supply
`--trusted-digest` with a digest pinned out of band and authenticity becomes
checkable. `verify` states the unestablished case explicitly rather than letting
a digest imply more than it shows.

### Compatibility

- Existing responses, SDK behaviour, MCP tools, feed semantics and Memory Pack
  authoritative semantics are unchanged. The receipt is purely additive and is
  absent unless requested.
- The Memory Pack schema stays at version 1. The proof reuses
  `MemoryPack.canonical_json` exactly, so there is one canonicalisation contract
  rather than two.
- Retrieval is unchanged. No tokenizer, threshold, acceptance-policy or
  candidate-selection behaviour was modified; M013's rejected rewrites stay
  rejected.

### Fixed

- Verifying a historical proof compared the observation cutoff as text, so a
  claim recorded at exactly `as_of` was rejected whenever the artifact spelled
  the instant `...Z` and the proof `...+00:00` — which is what a real response
  does. Instants are now compared as instants, as they already were for
  `valid_at`.
- A receipt for a supersession chain authored without validity intervals named
  today's claim for a question asked earlier. Supersession is the only record of
  observation time, so it is now the fallback.
- The packaging-boundary test skipped unless a wheel happened to be built first,
  which is never true in CI. It builds on demand now, and additionally installs
  the wheel with `--no-deps` into an empty virtual environment and verifies an
  exported receipt through the installed command, outside the source tree.

## [v0.7.0] - 2026-09-28

### Added

- Unified authoritative `context()` surface across REST, the Python SDK and MCP.
  The archive is resolved first and ranked live chunks are attached as raw
  material, so a conflicted key is reported as a conflict rather than as the
  latest writer's text. The response keeps its existing fields and adds
  `status`, `memories`, `conflicts` and `changes`; existing fields are unchanged.
- `memory_pack.py`, a standard-library-only reader for the Memory Pack. It is
  shipped in the distribution so a consumer can read a pack with no database,
  ORM, embedding model or server present.
- Claim-representation cache (migration `007_claim_embedding_cache`) and a
  `mindpalace reindex` command that rebuilds the derived layer from the archive.
- `as_of` and explicit `intent` selectors on `GET /api/context`, and a lexical
  relevance fallback so authoritative query answers when the embedding model
  cannot load.
- Dependency-free Memory Pack reading, verified from a clean install.
- **Corpus scoping for live retrieval.** `/api/search`, `/api/query` and
  `/api/context` now resolve an explicit corpus before retrieving. With a single
  corpus the parameter may be omitted; with several, omitting it returns **422**
  and naming an unknown corpus returns **404**, rather than silently searching
  across namespaces. Callers that relied on the previous cross-namespace default
  must pass `corpus`.
- **Sync reports per-file failures.** A sync response now carries a bounded list
  of `errors` (at most 50, each a path and a non-sensitive reason) alongside the
  existing aggregate counters, exposed through the SDK and the CLI.
- Semantic loading stays lazy: neither the embedder nor the reranker loads a
  model until first use, and the reranker is a thread-safe singleton.
- The OpenAPI document now reports the installed package version, so it cannot
  disagree with the distribution.

### Fixed

- **Cached-vector queries got 22x slower on a long-lived connection.** After
  asyncpg prepared the statement, two large SQL array parameters dominated the
  load: roughly 380 ms for the first five executions and about 8,400 ms after.
  The load now selects by the indexed corpus, model and dimension prefix and
  filters in Python, which is also a stricter freshness check. Measured on the
  public query path with a warm cache: 10,168 ms to 1,745 ms at 5,000 claims,
  1,359 ms to 1,010 ms at 1,000, and no regression at 100.
- `bounded_pack` now preserves the `NO_RELEVANT_MEMORY` marker, so a bounded
  answer to an unanswerable question is distinguishable from an empty envelope.
- The archive load no longer ships chunk text to the public projection, which
  reads only a chunk's id and heading.
- **Deleting a corpus whose durable archive still references it is refused** with
  **409** and a named error, instead of failing on a foreign key or removing
  manifest rows before documents.
- **A missing semantic model is reported as 503**, not 500, on ingest, search,
  query and context, and the dependency detail is not echoed to the caller.
- **Feed cursors are validated and canonicalised** against the corpus name before
  use, so a malformed or foreign cursor is rejected rather than compared.
- `memory_rehydrate` preserves the document id the archive recorded, so a rebuild
  cannot change `Source.document_id` in a replay.
- The API description reads "The Durable AI Memory Substrate" and is pinned by a
  test, so it cannot drift back to the previous RAG framing.
- `memory_pack` is declared in the package, so `pip install` makes it importable.
- Runtime dependencies are declared in `pyproject.toml` rather than left empty.

### Changed

- Claim representations are tokenised once and memoised on the immutable claim
  text, and the common-term difference is computed once instead of per candidate
  and per topic. Neither showed a measurable end-to-end change on the measuring
  host and neither is presented as a performance result.
- The public projection no longer receives full chunk text.
- Process startup no longer registers a no-op ASGI lifespan handler. Startup
  remains independent of PostgreSQL connectivity, which was the handler's only
  stated purpose.

### Compatibility

Backward compatible for the Memory Pack schema, which stays at version 1, and for
every public response field. The one behaviour a caller can notice is corpus
scoping on live retrieval: where several corpora exist, `corpus` is now required
and an omitted value returns 422 rather than searching all namespaces.

### Upgrade

- Two additive migrations apply: `006_snapshot_membership_seal` and
  `007_claim_embedding_cache`. Neither rewrites existing rows. The Memory Pack
  schema stays at version 1 and no public response contract changed, so this is a
  minor release.

### Known limitations

- Measured warm p50 for the public query path: 47 ms at 100 claims, 1.0 s at
  1,000, 1.7 s at 5,000 and 4.4 s at 10,000, on one host with roughly 2x
  run-to-run variance. 25,000 claims and above were not measured.
- Above about 5,000 claims, projection dominates: it builds a model per archived
  claim and keeps roughly one. Reducing that requires the dependency closure to
  be solved first, because narrowing the archive before projection was measured to
  change 32 of 202 benchmark answers.
- The archive load is not a clean linear function of archive size between 1,000
  and 5,000 versions, and its sort spills to disk on a corpus of that size. The
  cause is not identified. JIT compilation and run-to-run variance are ruled out.
- Conflict detection is keyed, so a contradicting claim authored under a different
  key is not detected and is not labelled as drift.
- Relationship questions score 31 of 34. The failures are two ranking
  weaknesses, one of which is a vocabulary gap, and one missing authored
  representation. No multi-hop relationship reasoning is claimed.
- M006.75 frozen evidence is unchanged. No prior benchmark number was rewritten.

## [v0.6.0] - 2026-09-24

### Added

- Durable PostgreSQL-backed corpus-scoped memory feed with keyset ordering,
  integrity-protected corpus-bound cursors, REST/Python SDK/CLI interfaces, and
  dependency-aware health endpoints.
- Isolated M009 release validation for live interface equivalence, concurrent
  insertion semantics, exact 100/1,000/10,000-version fixtures, p50/p95 timing,
  and PostgreSQL query plans.
- Explicit MCP exclusion decision for the operational feed.

### Guarantees and limitations

The feed provides bounded keyset continuation over `(observed_at, version_id)`
and documents its MVCC/late-commit behavior. It is not exactly-once messaging,
a broker, CDC, or transactional event delivery. The final page has no implicit
continuation cursor; polling clients must restart and deduplicate or maintain a
separate boundary policy. M009 does not change any M006.75/M007/M008 research
inputs, results, or claims.

Validation evidence is recorded in [`docs/m009/RELEASE_READINESS.md`](docs/m009/RELEASE_READINESS.md).

## [v0.5.1] - 2026-09-24

### Added

- M007.1 independent-adjudication handoff infrastructure.
- Blind 118-case review package with authoritative traceability.
- Ambiguity-preserving reviewer context for exact and ambiguous matches.
- Reviewer schema, guide, blank JSONL template, and reviewer-file validation.
- DecisionReceipt infrastructure with canonical fingerprints, explanation, replay,
  and structured drift detection.
- Deterministic synthetic semantic/property suites and executable M007–M008
  research runner.
- Archive reconstruction through the existing ingestion service.

### Validation

The latest full-suite validation snapshot before this release was:

```text
942 passed, 0 failed, 147 skipped, 10 warnings
```

Skipped tests are environment-gated integration tests and are not counted as
passes.

### Scientific status

- M006.75 remains the latest empirical benchmark: **47/60**.
- M007.1 is **ready for independent human adjudication**.
- M007 scientific results are pending two independent reviewer submissions.
- M008 evaluation remains downstream of the M007 scientific gate.

This release packages research/evaluation infrastructure only. It does not claim
M007 accuracy, agreement, calibration, or comparative improvement.

## [v0.5.0] - 2026-09-23

### Included milestones

- **M004:** persistent, versioned, corpus-scoped memory foundation.
- **M005:** public REST, Python SDK, MCP, and CLI memory interfaces.
- **M006:** persistent-memory evaluation infrastructure and lifecycle
  validation.
- **M006.5:** natural-language memory querying and bounded relevance access
  over the persistent memory model.
- **M006.75:** frozen, reproducible evaluation of current, historical,
  temporal, multi-topic, conflict/provenance, and abstention behavior.

### Added

- Immutable document/version history with deletion and restoration lifecycle.
- Authored claims, exact evidence, provenance, validity intervals,
  supersession, conflicts, snapshots, and replay.
- Multiple evidence references per claim.
- Bounded Memory Packs that preserve evidence and conflict closure.
- Public memory operations across REST, Python SDK, MCP, and CLI.
- Natural-language query planning with explicit abstention and temporal
  handling.
- Reproducible PostgreSQL-backed M006.75 benchmark runner and verification
  artifact.
- Frozen held-out corpus, frozen policy, source/dependency/model fingerprints,
  and canonical result validation.

### Evaluation

On the frozen 60-question M006.75 corpus:

| Category | Exact / full |
|---|---:|
| Overall | **47/60** |
| Current | 6/10 |
| Historical | 5/10 |
| Temporal | 10/10 |
| Multi-topic | 9/10 |
| Conflict / provenance | 7/10 |
| Abstention | 10/10 |

Safety results are reported separately: 0 false-positive retrievals, 0
false-current promotions, 0 provenance failures, 0 conflict failures, 0
temporal failures, 0 budget failures, and 0 execution failures.

Canonical result:

```text
881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8
```

### Limitations

The benchmark is a project-specific authored evaluation, not a claim of
universal memory-system superiority or production readiness. M007/M007.1
decision-provider and Jev research is intentionally unreleased.

## [v0.4.1] - 2026-09-17

- Stabilized the pre-M006 persistent-memory baseline and database URL handling.
- This release is the starting point for the M006 evaluation work.

## [v0.4.0] - 2026-09-17

- Exposed persistent memory through the public developer interfaces.

[v0.6.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.6.0
[v0.5.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.1
[v0.5.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.5.0
[v0.4.1]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.1
[v0.4.0]: https://github.com/raunakdey-07/mind-palace/releases/tag/v0.4.0

### Platform readiness

- **An error taxonomy** (`mindpalace_errors.py`, re-exported from the SDK). One
  base class, six components (`memory`, `auth`, `database`, `transport`,
  `provider`, `mindpalace`), a stable machine-readable `code`, `retryable`,
  `request_id`, and a bounded `details` dict. The existing HTTP `detail` shape is
  preserved and the new fields were added beside it, so nothing an integrator
  already reads changes. An unrecognised exception is reported as an internal
  error carrying its *type* and not its message.
- **Request ids.** Every request gets an `X-Request-Id`; an inbound one is
  honoured so a gateway's id survives, and a header that is not a short printable
  token is replaced. The middleware is registered outside the authentication
  middleware, so a refusal -- the request a user is about to report -- carries one.
- **Provider failures are classified, never forwarded.**
  `classify_provider_error` picks a category and writes the message from a fixed
  set, keeping the provider's request id. A provider error body routinely echoes
  the prompt and the key.
- **A provider conformance suite** (`tests/test_provider_conformance.py`) and
  `docs/providers.md`, separating the model-free memory contract from the internal
  embedding provider from the generation provider that belongs to the consuming
  application. No API key and no network: a conformance suite that needs a paid
  account to pass is a suite that stops being run.
- **A deletion and erasure lifecycle document** (`docs/deletion-and-erasure.md`),
  including the envelope-encryption design and an explicit statement that
  cryptographic erasure is **not** implemented.
- **An integration conformance kit** (`integration_conformance.py`), runnable
  against any installation, with the negative checks that would catch a
  misconfigured deployment.
- **A design note** (`docs/platform-readiness-design.md`) recording what was found,
  what was decided, and what was deliberately not done.

### Withdrawn

- **Resource-level authorization inside a corpus.** Designed, prototyped, tested,
  and then removed from the migration chain: it made `POST /api/memory/remember`
  return `503` for a write that has always succeeded, and the cause was not
  diagnosed to a conclusion. Shipping a migration that breaks the primary write
  path is not acceptable at any confidence level. The design, the three real
  findings it produced, the adversarial suite, and the reinstating steps are in
  [`docs/withdrawn/`](docs/withdrawn/README.md). Corpus-level isolation is
  unchanged and remains the shipped guarantee.
