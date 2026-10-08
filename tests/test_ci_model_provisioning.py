# SPDX-License-Identifier: Apache-2.0
"""The CI workflow must provision the models its own tests require.

A test suite that needs a model but whose CI never fetches one is not a flaky
suite, it is a broken one -- it passes on every developer machine that has the
cache and fails on every clean runner. That is exactly what happened before
`v0.9.0`: the `tests` job installed `sentence-transformers` but never the weights,
ran pytest with `HF_HUB_OFFLINE=1`, and 41 tests failed with `OSError: We couldn't
connect to ...`. `--maxfail=1` reported one of them.

Nobody notices a missing CI step by reading the CI file, so these tests read it for
them. They are cheap, they touch no model, and deleting or weakening the
provisioning step makes them fail.

What they pin:

* both jobs that run tests provision models before running them, and verify them
  before disabling the network;
* the pytest step still sets `HF_HUB_OFFLINE=1`, so the suite cannot quietly depend
  on the network again;
* pytest runs without `--maxfail=1`, which is what hid 40 further failures;
* both jobs call the same provisioning script, so a pinned revision cannot be
  updated in one place and forgotten in the other;
* the pinned revision of the embedder is the revision the frozen benchmark recorded,
  so CI and the released research artifact cannot drift apart;
* the cache is keyed on those revisions, so a revision bump invalidates it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "ci" / "provision_models.py"
ARTIFACT = ROOT / "eval" / "m00675" / "result.json"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps_of(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job].get("steps", [])


def names_of(workflow: dict, job: str) -> list[str]:
    return [s.get("name", "") for s in steps_of(workflow, job)]


def index_of(workflow: dict, job: str, needle: str) -> int:
    names = names_of(workflow, job)
    for i, name in enumerate(names):
        if needle in name:
            return i
    raise AssertionError(f"{job}: no step matching {needle!r} in {names}")


# The two jobs that execute pytest. The `wheel` job runs an offline proof check and
# needs no model; `build` is a Docker build.
TESTING_JOBS = ("tests", "five-minute-path")


def test_both_testing_jobs_provision_the_models(workflow):
    for job in TESTING_JOBS:
        assert index_of(workflow, job, "Provision") >= 0, f"{job} never provisions a model"


def test_both_testing_jobs_verify_before_the_network_goes_away(workflow):
    """Verification must precede the test step, or it proves nothing.

    The point is to fail in one clear line rather than as forty-one failures that
    surface forty minutes later.
    """
    for job in TESTING_JOBS:
        verify = index_of(workflow, job, "Verify the pinned models load offline")
        provision = index_of(workflow, job, "Provision the pinned model weights")
        assert provision < verify, f"{job}: verification must come after provisioning"
        assert verify >= 0


def test_verification_loads_through_the_application_offline(workflow):
    """`--verify` must run with offline mode set, and must use the script.

    Loading the model files directly would not prove Mind Palace can use them; the
    script loads through `Embedder` and `Reranker`, the classes the routers call.
    """
    for job in TESTING_JOBS:
        step = steps_of(workflow, job)[index_of(workflow, job, "Verify the pinned models")]
        run = step.get("run", "")
        assert "scripts/ci/provision_models.py --verify" in run, run
        assert step["env"]["HF_HUB_OFFLINE"] == "1", step.get("env")
        assert step["env"]["TRANSFORMERS_OFFLINE"] == "1", step.get("env")


def test_provisioning_steps_run_with_the_network_available(workflow):
    """The download needs the network; only the tests must not."""
    for job in TESTING_JOBS:
        step = steps_of(workflow, job)[index_of(workflow, job, "Provision the pinned model")]
        assert step["env"]["HF_HUB_OFFLINE"] == "0", step.get("env")
        assert step["env"]["TRANSFORMERS_OFFLINE"] == "0", step.get("env")
        assert "scripts/ci/provision_models.py" in step.get("run", "")


def test_pytest_still_runs_offline(workflow):
    """The reproducibility boundary: models local, network unused."""
    step = steps_of(workflow, "tests")[index_of(workflow, "tests", "Run tests")]
    assert step["env"]["HF_HUB_OFFLINE"] == "1", step.get("env")
    assert step["env"]["TRANSFORMERS_OFFLINE"] == "1", step.get("env")


def test_pytest_does_not_stop_at_the_first_failure(workflow):
    """`--maxfail=1` is what turned 41 failures into one reported failure."""
    run = steps_of(workflow, "tests")[index_of(workflow, "tests", "Run tests")].get("run", "")
    assert "--maxfail" not in run, (
        "--maxfail hides the scope of a failure; that is how 41 model failures were "
        "reported as one"
    )
    assert "pytest" in run


def test_both_jobs_share_one_provisioning_script(workflow):
    """A second copy of this logic is how the jobs drift and only one keeps working."""
    for job in TESTING_JOBS:
        provision = steps_of(workflow, job)[index_of(workflow, job, "Provision the pinned model")]
        assert "scripts/ci/provision_models.py" in provision.get("run", "")
    # Prose may mention `snapshot_download` -- the comment explaining *why* revisions
    # are pinned does. Executable inline download is not, because then bumping a
    # revision would mean editing two files and only one of them is tested.
    executable = [
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if "snapshot_download" in line and not line.lstrip().startswith("#")
    ]
    assert not executable, (
        "inline model download in the workflow; keep it in "
        f"scripts/ci/provision_models.py: {executable}"
    )


def test_the_pinned_revision_is_the_one_the_benchmark_recorded():
    """The pin is not invented. It is the revision the frozen artifact was measured with.

    If these ever diverge, CI would be verifying the suite against different weights
    than the ones the released benchmark result describes -- which is precisely the
    kind of quiet drift this repository keeps being bitten by.
    """
    recorded = json.loads(ARTIFACT.read_text(encoding="utf-8"))["model"]
    pins = _pins()
    assert pins["EMBEDDING_MODEL"] == recorded["revision"], (
        f"the CI pin {pins['EMBEDDING_MODEL']} no longer matches the revision recorded "
        f"in eval/m00675/result.json ({recorded['revision']}). Pinning something else "
        "means CI verifies the suite against weights the released result never used."
    )


def test_pins_are_exact_commit_shas():
    """A branch name is not a pin. `main` is a promise about the future."""
    pins = _pins()
    assert set(pins) == {"EMBEDDING_MODEL", "RERANKER_MODEL"}, pins
    for name, revision in pins.items():
        assert re.fullmatch(r"[0-9a-f]{40}", revision), f"{name} is not a pinned sha: {revision}"


def test_both_pins_appear_in_the_cache_keys(workflow):
    """A cache keyed on less than the revisions serves the wrong model after a bump."""
    pins = _pins()
    for job in TESTING_JOBS:
        steps = steps_of(workflow, job)
        cache = [s for s in steps if s.get("uses", "").startswith("actions/cache")]
        assert cache, f"{job}: no model cache"
        key = cache[0]["with"]["key"]
        for name, revision in pins.items():
            assert revision in key, f"{job}: cache key omits the {name} revision ({key})"
        assert "runner.os" in key, f"{job}: cache key must not cross operating systems"
        assert cache[0]["with"]["path"] == "~/.cache/huggingface", cache[0]["with"]["path"]


def test_the_cache_holds_no_credentials(workflow):
    """GitHub warns against caching secrets. Both models are public; no token is used."""
    for job in TESTING_JOBS:
        steps = steps_of(workflow, job)
        for step in steps:
            if step.get("uses", "").startswith("actions/cache"):
                blob = json.dumps(step)
                for forbidden in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "secrets."):
                    assert forbidden not in blob, f"{job}: {forbidden} in the cache step"


def test_pip_and_model_caches_are_separate(workflow):
    """One opaque cache is how you end up debugging two problems at once."""
    steps = steps_of(workflow, "tests")
    setup = [s for s in steps if s.get("uses", "").startswith("actions/setup-python")]
    assert (
        setup and setup[0]["with"].get("cache") == "pip"
    ), "pip cache should stay with setup-python"
    for step in steps:
        if step.get("uses", "").startswith("actions/cache"):
            assert step["with"]["path"] == "~/.cache/huggingface", step["with"]["path"]


def _pins() -> dict[str, str]:
    """Read the pins out of the script, so there is one definition."""
    text = SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"PINS: dict\[str, str\] = \{(.*?)\}", text, re.DOTALL)
    assert block, "PINS not found in scripts/ci/provision_models.py"
    return dict(re.findall(r'"(\w+)":\s*"([0-9a-f]{40})"', block.group(1)))
