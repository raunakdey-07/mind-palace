"""4-way agency readiness evaluation (deterministic baseline)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BRANDS_DIR = Path(__file__).parent / "brands"


def load_brands():
    return {p.stem: p.read_text() for p in BRANDS_DIR.glob("*.md")}


def main():
    brands = load_brands()
    print(json.dumps({"brands": list(brands), "count": len(brands)}, indent=2))


if __name__ == "__main__":
    main()