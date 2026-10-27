"""Audit regression suite (Stage A). Stage B defects are xfail(strict)."""

from __future__ import annotations
import pytest


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_approved_prohibition_curated():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_approved_prose_plain_language():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_descriptive_mustard_yellow():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_v2_top_of_list_insertion():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_hard_wrapped_prose():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_mixed_bullet_prose_table():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_descriptive_voice_no_modal():
    raise AssertionError("trap not yet handled")


@pytest.mark.xfail(strict=True, reason="Fixed in Stage B v0.10.0")
def test_unapproved_september_brainstorm():
    raise AssertionError("trap not yet handled")
