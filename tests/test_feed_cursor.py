from datetime import datetime, timezone

import pytest

from api.services.memory_feed import decode_cursor, encode_cursor
from api.services.memory_public import MemoryError


def test_cursor_is_corpus_bound_and_tamper_evident():
    cursor = encode_cursor("corpus-a", datetime(2026, 1, 1, tzinfo=timezone.utc), "v1")
    assert decode_cursor(cursor, "corpus-a") == (datetime(2026, 1, 1, tzinfo=timezone.utc), "v1")
    with pytest.raises(MemoryError) as mismatch:
        decode_cursor(cursor, "corpus-b")
    assert mismatch.value.code == "cursor_corpus_mismatch"
    with pytest.raises(MemoryError) as tampered:
        decode_cursor(cursor[:-1] + ("a" if cursor[-1] != "a" else "b"), "corpus-a")
    assert tampered.value.code == "invalid_cursor"


def test_cursor_rejects_malformed_input():
    with pytest.raises(MemoryError) as exc:
        decode_cursor("not-a-cursor", "corpus-a")
    assert exc.value.code == "invalid_cursor"
