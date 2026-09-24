"""Shared test fixtures for Mind Palace."""

import os

import pytest

# M009 cursor tests exercise the real HMAC boundary. Production configuration
# supplies this value explicitly; tests use a deterministic non-secret value.
os.environ.setdefault("MIND_PALACE_CURSOR_SECRET", "m009-test-cursor-secret-" + "x" * 48)


@pytest.fixture
def sample_markdown() -> str:
    return """---
title: "Test Project"
date: 2024-01-15
tags: ["test", "ai"]
status: Completed
summary: "A test project for validation"
---

# Introduction

This is the introduction section of the test project.

## Methods

We used several methods including:
- Data collection
- Model training
- Evaluation

## Results

The results show significant improvement over baseline.
"""


@pytest.fixture
def sample_query_text() -> str:
    return "What methods were used in the test project?"
