---
title: "Kaggle: ASL Fingerspelling Recognition"
date: 2023-12-05
tags: ["kaggle", "deep-learning", "sequence"]
document_type: "kaggle"
competition: "ASL Fingerspelling"
status: Completed
summary: "American Sign Language fingerspelling from hand landmarks with CTC loss"
---

# ASL Fingerspelling Recognition

Landmark sequences → spelled characters, using **CTC loss** for alignment-free training.

## Approach

- Input: 21 hand landmark coordinates per frame (no raw video)
- Transformer encoder over landmark sequences; CTC head for character alignment
- Coordinate normalization relative to the palm center for rotation invariance
- Character-level vocabulary with beam-search decoding

## Results

Levenshtein distance 1.8 per phrase. Landmark-based input made the problem tractable on CPU-class hardware — representation choice as the decisive factor.
