# Provider integration

Mind Palace has three distinct relationships with models, and conflating them is
how a memory subsystem ends up coupled to somebody else's vendor. They are
separate here, deliberately.

| Concern | Who provides it | Coupling |
|---|---|---|
| **A. The memory contract** | Mind Palace | none — you call it over HTTP or the SDK |
| **B. Internal retrieval** (embeddings, reranking) | Mind Palace, via a narrow protocol | swappable behind a protocol |
| **C. Generation** (writing prose from memory) | **your application** | none — Mind Palace does not generate |

## A. The memory contract is model-free

You can retrieve authoritative memory, its provenance, temporal state, evidence
and receipts without using any particular generation model, because none of those
are produced by one. `MemoryResponse` has no vendor in it.

The one thing generation is used for is `/api/query/ask` in `rag` mode, and it is
optional: `mode: "rag"` returns memory, `mode: "ask"` asks a model. If you bring
your own model, use `rag` and keep the generation entirely on your side. That is
the recommended path, and the reference integration does exactly that.

## B. Internal retrieval providers

### The protocol

An embedder must satisfy `api/services/embedder.py::Embedder`:

```python
class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...      # e.g. "all-MiniLM-L6-v2"
    @property
    def dimension(self) -> int: ...       # must equal the index's
    @property
    def version(self) -> str: ...         # "{model_name}:{dimension}", stored per chunk
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_single(self, text: str) -> list[float]: ...
```

That is the whole contract. `embed([])` must return `[]`, not raise — an empty
corpus is a legitimate state, not a failure. Vectors must be L2-normalised, because
the index compares them by cosine distance and a mix of normalised and
unnormalised vectors silently returns wrong neighbours.

The default provider is `sentence_transformers` with `all-MiniLM-L6-v2`, chosen by
`EMBEDDING_MODEL`. It is the verified one, and it is the only one with test
coverage against the real index.

### Model identity is enforced, not assumed

Every chunk stores `embedding_model`, `embedding_dimension` and
`embedding_version`. Before a semantic query runs, the provider's identity is
compared against what the index actually holds, and a mismatch is **refused**
rather than served.

This is not defensive decoration. Silently mixing vectors from two models in one
index returns confidently wrong neighbours — no error, plausible answers, wrong
results. That is the worst failure mode this system has, so it is made loud:
`ProviderIncompatibleModelError`, which is not retryable.

Changing `EMBEDDING_MODEL` on a populated corpus therefore requires an explicit
re-embedding migration. Mind Palace does not perform that migration for you,
because there is no safe automatic version of it: the old vectors are
meaningless under the new model and must be recomputed from the archived content,
not approximated.

### Reranking

`api/services/reranker.py` is the same shape: a `SentenceTransformer` cross-encoder
over an **already bounded** candidate set. The protocol is `rerank(query,
candidates) -> ordered candidates`. It is optional, and a reranker is never
allowed to introduce a candidate that recall did not produce — reordering is the
whole of its job, so a reranker that widened the set would change what "top k"
means.

### The conformance suite

`tests/test_provider_conformance.py` runs any candidate provider through the same
contract the built-in one satisfies: correct dimensionality, correct identity,
empty input, batch behaviour, malformed output, wrong dimensions, timeouts, and
unavailability. It uses deterministic fakes and needs no API key and no network,
so ordinary CI runs it.

## C. Generation belongs to your application

`api/services/llm.py` exists for the optional `/ask` mode and already speaks to
Ollama and any OpenAI-compatible endpoint by environment variable. **It is not
part of the memory contract**, and no vendor SDK is a dependency of this package.

For anything beyond the optional `ask` mode, write your own adapter. A generation
provider belongs in your application because:

* you already have one, and adopting Mind Palace must not mean replacing it;
* your prompts, your provider's data handling terms, and your retry policy are
  yours;
* a "universal" orchestration layer inside a memory subsystem would be a
  framework, which this project is not.

A minimal adapter is a function with one signature. Anything a provider adapter
needs to express — streaming, tool calls, a fallback chain — stays on your side
of that boundary.

## Provider failures are not no-results

When a provider fails, Mind Palace raises a typed error and says which category of
failure it was. It never returns an empty result set, because an empty result is a
claim about what the store contains and a provider outage is not.

| Situation | Error | `retryable` |
|---|---|---|
| Credentials or configuration rejected | `ProviderAuthenticationError` | no |
| No answer in time | `ProviderTimeoutError` | **yes** |
| Rate limited | `ProviderRateLimitError` | **yes** |
| Temporarily unavailable | `ProviderUnavailableError` | **yes** |
| Answered with something unusable | `ProviderMalformedResponseError` | no |
| Model identity or dimension mismatch | `ProviderIncompatibleModelError` | no |
| Understood and declined | `ProviderRefusedError` | no |

All of them carry `component: "provider"`, so `except ProviderError` catches the
category and the `code` distinguishes the cause. Only the ones marked retryable
are ever retried, and only by the caller.

The provider's own error text is **not** forwarded — it routinely echoes the
prompt, the key, or the request body. Its request id is kept, because that is what
a provider's support desk asks for.

## Installing without a provider

The core package has no vendor SDK dependency. `sentence_transformers` is required
for the built-in embedder and is an ordinary install dependency; the MCP server
and generation adapters are extras, and CI verifies a bare install stays bare.
A provider adapter that needs a vendor SDK belongs behind an extra, so that
installing Mind Palace never pulls in somebody else's client library.