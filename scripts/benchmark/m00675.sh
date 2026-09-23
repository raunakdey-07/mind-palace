#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${PYTHON:-$root/venvmp/bin/python}"
mode="${1:---clean}"
if [[ ! -x "$python_bin" ]]; then
  echo "FAIL CLOSED: Python interpreter not found: $python_bin" >&2
  exit 1
fi
if [[ "$mode" == "--verify" ]]; then
  PYTHONPATH="$root" "$python_bin" "$root/scripts/benchmark/m00675.py" \
    --verify "$root/eval/m00675/result.json"
  exit 0
fi
if [[ "$mode" != "--clean" ]]; then
  echo "Usage: $0 [--clean|--verify]" >&2
  exit 2
fi
: "${DATABASE_URL:?Set DATABASE_URL to the dedicated PostgreSQL benchmark database}"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

PYTHONPATH="$root" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  "$python_bin" -m api.services.memory_query_benchmark \
  --save "$tmp" --reliability heldout \
  --policy-file "$root/eval/memory_query_policy.json"

PYTHONPATH="$root" "$python_bin" "$root/scripts/benchmark/m00675.py" \
  --input "$tmp" --output "$root/eval/m00675/result.json"
cat "$root/eval/m00675/result.json"
