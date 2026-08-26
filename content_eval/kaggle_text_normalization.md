---
title: "Kaggle: Text Normalization Challenge"
date: 2023-01-15
tags: ["kaggle", "nlp", "sequence"]
document_type: "kaggle"
competition: "Text Normalization"
status: Completed
summary: "Sequence-to-sequence text normalization with RNNs and rule-based hybrids"
---

# Text Normalization Challenge

Expanding "128km" → "one hundred twenty-eight kilometers" — the classic seq2seq problem.

## Approach

- Per-class models (numbers, dates, measures, ordinals) rather than one global model
- Rule-based fallback for regular classes; neural only for ambiguous ones
- Character-level **GRU** seq2seq with attention

## Results

Per-class decomposition beat the single model decisively. The hybrid rules-plus-neural pattern reappeared in later parsing projects.
