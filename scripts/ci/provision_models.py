# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Provision the model weights CI needs, at pinned revisions, and prove they load.

This exists because the alternative was a green local run and a red CI run. The
`tests` job installed `sentence-transformers` but never the weights, and then ran
pytest with `HF_HUB_OFFLINE=1`, so a clean runner had the package and not the model.
41 tests failed on `OSError: We couldn't connect to ...` -- every test that indexes
or ranks anything. A developer machine already had the cache, so the gap was
invisible from where it was being written.

Two things are pinned, deliberately:

* **Exact revisions, not branches.** `snapshot_download("repo")` resolves whatever
  `main` is today, which means the suite can change under you and a green run says
  nothing about next week's. The embedder's pin is not invented: it is the revision
  recorded in the frozen `eval/m00675/result.json`, so pinning it keeps the
  benchmark's model identity the one it was measured with.
* **`refs/main` is declared, not inherited.** `huggingface_hub` writes `refs/main`
  only when it resolves a branch. Downloading a bare commit sha leaves no `refs/main`,
  and `scripts/benchmark/m00675.py::model_fingerprint` reads that file to identify
  the model -- so a pinned download would make the frozen benchmark's own model check
  fail. Writing `refs/main` to the pinned sha is what makes the pin authoritative:
  the environment states "main is exactly this revision" instead of inheriting
  whatever the Hub points at.

Verification loads each model through the application's own code path -- `Embedder`
and `Reranker`, the same classes the routers use. Checking that a directory exists
would prove nothing about whether the model is loadable, which is the thing that has
been breaking.

Run it twice, as CI does:

    provision_models.py            # download at the pinned revisions
    provision_models.py --verify   # load them, offline, through the app

Neither this script nor anything else stores credentials. Both models are public, so
no token is needed and none should be added to the cache.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: Model identifier -> pinned commit sha.
#:
#: `EMBEDDING_MODEL` and `RERANKER_MODEL` are the application's own overrides, kept
#: in step with `api/services/embedder.py` and `api/services/reranker.py`. A pinned
#: revision only means anything if the identifier still matches the one the
#: application asks for, so `--verify` compares them.
PINS: dict[str, str] = {
    "EMBEDDING_MODEL": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "RERANKER_MODEL": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
}

#: How each application's default identifier maps onto a Hub repository id.
#: The embedder's default is the bare model name and sentence-transformers owns it;
#: the reranker's default is already fully qualified.
QUALIFIER = {"EMBEDDING_MODEL": "sentence-transformers/", "RERANKER_MODEL": ""}


def repo_id(env_var: str, environ: dict) -> str:
    """The Hub repository for one model, honouring the application's own override."""
    default = (
        "all-MiniLM-L6-v2"
        if env_var == "EMBEDDING_MODEL"
        else ("cross-encoder/ms-marco-MiniLM-L-6-v2")
    )
    name = environ.get(env_var) or default
    return name if "/" in name else QUALIFIER[env_var] + name


def cache_dir() -> Path:
    """Where `huggingface_hub` keeps its cache.

    `HF_HOME` wins if set, matching `huggingface_hub`. Otherwise
    `~/.cache/huggingface`, which is the default and therefore what `actions/cache`
    should target.
    """
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"])
    return Path.home() / ".cache" / "huggingface"


def model_dir(repo: str) -> Path:
    """`huggingface_hub`'s on-disk layout: `models--owner--name`."""
    return cache_dir() / "hub" / ("models--" + repo.replace("/", "--"))


def declare_main(repo: str, revision: str) -> Path:
    """Point `refs/main` at the pinned revision.

    Only written when the snapshot exists, so this can never claim a revision is
    present when it is not.
    """
    snapshot = model_dir(repo) / "snapshots" / revision
    if not snapshot.is_dir():
        return snapshot
    ref = model_dir(repo) / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(revision, encoding="utf-8")
    return snapshot


def provision() -> int:
    """Download every pinned snapshot, then declare each one as `main`."""
    from huggingface_hub import snapshot_download

    print(f"cache: {cache_dir()}")
    for env_var, revision in PINS.items():
        repo = repo_id(env_var, os.environ)
        print(f"\n{env_var}")
        print(f"  repository: {repo}")
        print(f"  revision:   {revision}")
        snapshot = Path(snapshot_download(repo_id=repo, revision=revision))
        snapshot = declare_main(repo, revision)
        if not snapshot.is_dir():
            print(f"  FAILED: snapshot missing at {snapshot}")
            return 1
        print(f"  snapshot:   {snapshot}")

    print("\nprovisioning complete; now run with --verify")
    return 0


def verify() -> int:
    """Prove both models load, offline, through the application's own classes.

    Deliberately imports `api.services.embedder.Embedder` and
    `api.services.reranker.Reranker` rather than loading the files directly: the
    question is whether Mind Palace can use the model, not whether the model exists.
    """
    problems: list[str] = []

    # 1. The identifiers the application asks for must be the ones we pinned.
    from api.services.embedder import DEFAULT_MODEL
    from api.services.reranker import Reranker

    pinned_embed_repo = repo_id("EMBEDDING_MODEL", {})
    pinned_rerank_repo = repo_id("RERANKER_MODEL", {})
    live_embed_repo = repo_id("EMBEDDING_MODEL", os.environ)
    live_rerank_repo = repo_id("RERANKER_MODEL", os.environ)
    if pinned_embed_repo != live_embed_repo:
        problems.append(
            f"EMBEDDING_MODEL is {live_embed_repo!r} but the pin covers "
            f"{pinned_embed_repo!r}; the application and the pin have diverged"
        )
    if pinned_rerank_repo != live_rerank_repo:
        problems.append(
            f"RERANKER_MODEL is {live_rerank_repo!r} but the pin covers "
            f"{pinned_rerank_repo!r}; the application and the pin have diverged"
        )
    if DEFAULT_MODEL != "all-MiniLM-L6-v2":
        problems.append(
            f"api.services.embedder.DEFAULT_MODEL changed to {DEFAULT_MODEL!r}; "
            "update PINS in this script"
        )

    # 2. `refs/main` must name the pinned revision, or a later unpinned download
    #    would silently replace what the tests were verified against.
    for env_var, revision in PINS.items():
        repo = repo_id(env_var, os.environ)
        ref = model_dir(repo) / "refs" / "main"
        if not ref.is_file():
            problems.append(f"{repo}: no refs/main -- was provisioning run?")
            continue
        recorded = ref.read_text(encoding="utf-8").strip()
        if recorded != revision:
            problems.append(f"{repo}: refs/main is {recorded!r}, expected the pinned {revision!r}")
        if not (model_dir(repo) / "snapshots" / revision).is_dir():
            problems.append(f"{repo}: snapshot for pinned revision {revision} is absent")

    # 3. Load them the way the application does.
    try:
        from api.services.embedder import Embedder

        embedder = Embedder()
        vectors = embedder.embed(["model provisioning check"])
        if not vectors or not vectors[0]:
            problems.append("Embedder returned no vector")
        else:
            print(f"Embedder loaded {embedder.version}, dimension {embedder.dimension}")
    except Exception as exc:  # noqa: BLE001 - any failure here is a provisioning failure
        problems.append(f"Embedder failed to load: {type(exc).__name__}: {exc}")

    try:
        reranker = Reranker()
        scores = reranker.score(
            "what datastore do we use", "The production datastore is PostgreSQL."
        )
        if not scores:
            problems.append("Reranker returned no scores")
        else:
            print(f"Reranker loaded {reranker.model_name}")
    except Exception as exc:  # noqa: BLE001 - as above
        problems.append(f"Reranker failed to load: {type(exc).__name__}: {exc}")

    if problems:
        print("\nMODEL PROVISIONING FAILURE", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nFix the provisioning step rather than the tests. These models are "
            "declared dependencies; a runner that lacks them cannot exercise the "
            "default read path.",
            file=sys.stderr,
        )
        return 1

    print("\nMODEL PROVISIONING OK: both models load offline at their pinned revisions")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="load the pinned models offline through the application, and fail loudly",
    )
    args = parser.parse_args()
    return verify() if args.verify else provision()


if __name__ == "__main__":
    raise SystemExit(main())
