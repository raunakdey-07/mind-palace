---
title: "Kaggle: Bengali.AI Handwritten Grapheme Classification"
date: 2023-08-12
tags: ["kaggle", "computer-vision", "classification"]
document_type: "kaggle"
competition: "Bengali.AI"
status: Completed
summary: "Multi-head grapheme classification over grapheme root, vowel, and consonant diacritics"
---

# Bengali Grapheme Classification

Handwritten Bengali characters decomposed into three parallel labels: grapheme root, vowel diacritic, consonant diacritic.

## Approach

- Single CNN backbone with **three classification heads**
- Per-head loss weighting tuned by sweep
- 4208 total class combinations from only ~7×11×168 free labels — combinatorial generalization
- Mixup augmentation helped; heavy geometric augmentation hurt stroke recognition

## Results

Recall 0.965 macro. The multi-head decomposition was the intended lesson: predicting components generalizes better than predicting the full combination.
