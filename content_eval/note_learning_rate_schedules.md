---
title: "Note: Learning Rate Schedules and Warmup"
date: 2024-01-02
tags: ["ml-fundamentals", "deep-learning", "note"]
document_type: "note"
status: Complete
summary: "Cosine decay, warmup, and one-cycle schedules — when each helps"
---

# Learning Rate Schedules

Training-stability notes from transformer fine-tuning work.

## Schedules

- **Warmup**: linear ramp over the first ~10% of steps; essential for transformer stability (used in every BERT/DeBERTa fine-tune)
- **Cosine decay**: smooth anneal to near zero; the default companion to warmup
- **One-cycle**: fast super-convergence for smaller CNNs
- **Reduce-on-plateau**: sensible for tabular NNs without step budgets

## Practical Rules

Fine-tuning pretrained transformers needs peak learning rates 1–2 orders of magnitude below pretraining. If loss spikes appear, lower the peak or lengthen warmup before blaming the data.
