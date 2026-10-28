from benchmarks.agency_readiness.checker import normalize, present, score


def test_normalize_case():
    assert normalize("Warm, a little dry, and confident") == "warm a little dry and confident"


def test_present_whole_token():
    assert not present("cure", "curated")


def test_present_mid_sentence_cut():
    assert not present("warm a little dry and confident", "warm a little dry and")


def test_score_all_rules():
    gold = {"standing_prohibitions": ["curated"], "required_voice_descriptors": [], "approved_specs": [], "superseded_v1_markers": [], "unapproved_draft_markers": []}
    ctx = "Do not use the word curated."
    assert score(ctx, gold)["rule_recall"] == 1.0


def test_score_empty():
    gold = {"standing_prohibitions": ["curated"], "required_voice_descriptors": [], "approved_specs": [], "superseded_v1_markers": [], "unapproved_draft_markers": []}
    assert score("", gold)["rule_recall"] == 0.0


def test_score_violation():
    gold = {"standing_prohibitions": [], "required_voice_descriptors": [], "approved_specs": [], "superseded_v1_markers": ["enthusiastic playful tone"], "unapproved_draft_markers": []}
    assert score("enthusiastic playful tone", gold)["violation"] == 1
