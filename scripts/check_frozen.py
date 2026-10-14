"""Frozen evidence: committed, hashed, and checked on every build.

    python scripts/check_frozen.py              # verify against the manifest
    python scripts/check_frozen.py --write      # regenerate the manifest

## What this is for, and what it is not

The guard this replaces was:

```
git diff v0.9.0..HEAD -- eval/ docs/performance/baseline-packs.json
```

which has been quoted in `docs/PROGRESS.md` since Session 1 as the proof that
frozen evidence did not move.

**It proves something much weaker than it looks.** `git diff` only sees files
git tracks. Nine artifacts under `eval/results/` are gitignored, and
`docs/performance/evidence/pytest-head.xml` is a tracked artifact that a
workstation run overwrites. A clean diff is consistent with all three
situations and cannot tell them apart from a real change.

So the guard is **two things, and the second one is the point**:

1. `git diff` over the tracked set, which is fast and exact for what it covers.
2. **SHA-256 over the bytes**, from a manifest committed alongside the files. A
   file that is missing, renamed or edited fails, whether git was ever asked.

The manifest is the evidence. The diff is a convenience.

## What "frozen" covers

Everything under `eval/` that git tracks, plus the benchmark artifacts under
`docs/performance/` that `docs/STATUS.md` cites. `eval/results/` is deliberately
**not** covered -- it is ignored, and see the header of
`docs/PROGRESS.md` Session 10 §1d for why a git-based guard cannot reach it.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "performance" / "frozen-manifest.json"

#: Directories whose tracked contents are frozen.
FROZEN_DIRS = ("eval",)

#: Individually frozen artifacts. A directory here would work; these are named so
#: that adding one is a deliberate act rather than a side effect of a glob.
FROZEN_FILES = (
    "docs/performance/baseline-packs.json",
    "docs/performance/m0115-experiments.json",
    "docs/performance/m0130-heldout.json",
    "docs/performance/quality-frame.json",
    "docs/performance/heldout-v2-freeze.json",
    "docs/performance/heldout-v3-freeze.json",
    "docs/performance/retrieval-lab-protocol.freeze.json",
    "docs/performance/downstream-eval-protocol.freeze.json",
)


def tracked() -> set[str]:
    return set(
        subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout.split()
    )


def frozen_paths() -> list[str]:
    known = tracked()
    out = {
        path
        for base in FROZEN_DIRS
        for path in known
        if path.startswith(base + "/") and not path.endswith(".pyc")
    }
    out |= {p for p in FROZEN_FILES if p in known}
    return sorted(out)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def write_manifest() -> int:
    import json

    paths = frozen_paths()
    body = {
        "note": (
            "SHA-256 of every frozen evidence file, committed alongside them. "
            "Regenerate with scripts/check_frozen.py --write, and only when a "
            "change to frozen evidence is intended and recorded in "
            "docs/PROGRESS.md first."
        ),
        "algorithm": "sha256",
        "count": len(paths),
        "files": {p: digest(ROOT / p) for p in paths},
    }
    MANIFEST.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    print(f"wrote {MANIFEST.relative_to(ROOT)} with {len(paths)} files")
    return 0


def verify() -> int:
    import json

    if not MANIFEST.exists():
        print(f"::error::no manifest at {MANIFEST.relative_to(ROOT)}")
        print("Run: python scripts/check_frozen.py --write")
        return 1
    body = json.loads(MANIFEST.read_text())
    recorded: dict[str, str] = body["files"]

    problems: list[str] = []
    for path, expected in sorted(recorded.items()):
        target = ROOT / path
        if not target.exists():
            problems.append(f"{path}: MISSING -- frozen evidence deleted or renamed")
            continue
        actual = digest(target)
        if actual != expected:
            problems.append(f"{path}: CHANGED -- {expected[:12]} -> {actual[:12]}")

    current = set(frozen_paths())
    added = sorted(current - set(recorded))
    if added:
        problems.append(
            f"{len(added)} frozen path(s) not in the manifest: {', '.join(added[:5])}"
            + (" ..." if len(added) > 5 else "")
            + " -- run scripts/check_frozen.py --write if that is intended"
        )

    print(f"frozen files in manifest : {len(recorded)}")
    print(f"frozen files on disk     : {len(current)}")
    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for problem in problems:
            print(f"  !!  {problem}")
        return 1
    print("\nevery frozen evidence file matches its recorded SHA-256")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="regenerate the manifest")
    args = parser.parse_args()
    return write_manifest() if args.write else verify()


if __name__ == "__main__":
    sys.exit(main())
