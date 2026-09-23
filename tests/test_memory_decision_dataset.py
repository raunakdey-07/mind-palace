"""Development dataset construction must not consume frozen held-out data."""

from api.services.memory_decision_dataset import (
    dataset_integrity_summary,
    load_development_examples,
)


def test_development_dataset_has_authored_positive_and_hard_negative_pairs():
    examples = load_development_examples()
    assert len(examples) > 100
    assert any(example.relevance == "positive" for example in examples)
    assert any(example.hard_negative for example in examples)
    assert all(example.question_id.startswith("question-") for example in examples)
    assert all(example.temporal == "unknown" for example in examples)


def test_abstention_examples_are_not_silently_labeled_negative():
    examples = load_development_examples()
    abstention = [example for example in examples if "unrelated-" in example.question_id]
    assert abstention
    assert all(example.relevance == "unknown" for example in abstention)
    assert all(example.subject == "unknown" for example in abstention)


def test_frozen_questions_are_excluded():
    examples = load_development_examples()
    assert not any(example.question_id.startswith("heldout-") for example in examples)


def test_structural_negatives_are_explicitly_unadjudicated():
    summary = dataset_integrity_summary(load_development_examples())
    assert summary["unadjudicated"] > 0
    assert summary["authored"] > 0
    assert summary["unknown"] > 0
    assert (
        summary["authored"] + summary["unadjudicated"] + summary["unknown"] == summary["examples"]
    )
