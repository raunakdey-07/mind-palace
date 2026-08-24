"""Tests for the reranker service."""

import os
from unittest.mock import MagicMock, patch

import pytest

from api.services.reranker import Reranker


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Isolate singleton state between tests."""
    Reranker._instance = None
    yield
    Reranker._instance = None


def test_reranker_singleton():
    """Test that Reranker follows singleton pattern."""
    with patch("api.services.reranker.CrossEncoder") as mock_ce:
        mock_ce.return_value = MagicMock()
        r1 = Reranker()
        r2 = Reranker()
        assert r1 is r2
        # Model should only be loaded once
        mock_ce.assert_called_once()


def test_reranker_score_returns_floats():
    """Test that score() returns plain Python floats."""
    reranker = Reranker()
    # Mock the CrossEncoder.predict to avoid model download
    with patch.object(reranker, "_model") as mock_model:
        mock_model.predict.return_value = [0.1, 0.5, 0.9]
        scores = reranker.score("test query", ["doc1", "doc2", "doc3"])

    assert len(scores) == 3
    assert all(isinstance(s, float) for s in scores)
    assert scores == [0.1, 0.5, 0.9]


def test_reranker_score_empty_documents():
    """Test that score() handles empty document list."""
    reranker = Reranker()
    scores = reranker.score("test query", [])
    assert scores == []


def test_reranker_score_changes_ordering():
    """Test that reranking can change document ordering."""
    reranker = Reranker()
    # Mock scores that would reorder documents
    with patch.object(reranker, "_model") as mock_model:
        # Documents provided in order: ["low", "high", "medium"]
        # But scores indicate: "high" should be first, then "medium", then "low"
        mock_model.predict.return_value = [0.2, 0.9, 0.5]
        scores = reranker.score("test query", ["low", "high", "medium"])

    # Verify scores are different, indicating reranking would change order
    assert scores[0] < scores[1]  # low score < high score
    assert scores[1] > scores[2]  # high score > medium score


def test_reranker_uses_env_model():
    """Test that Reranker reads model from environment variable."""
    with patch.dict(os.environ, {"RERANKER_MODEL": "custom-model"}):
        with patch("api.services.reranker.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            Reranker()
            mock_ce.assert_called_once_with("custom-model")


def test_reranker_default_model():
    """Test that Reranker uses default model when env var not set."""
    env = {k: v for k, v in os.environ.items() if k != "RERANKER_MODEL"}
    with patch.dict(os.environ, env, clear=True):
        with patch("api.services.reranker.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            Reranker()
            mock_ce.assert_called_once_with("cross-encoder/ms-marco-MiniLM-L-6-v2")
