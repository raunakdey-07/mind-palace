---
title: "Kaggle: Allen Institute Cell Line Classification"
date: 2024-01-28
tags: ["kaggle", "computer-vision", "multilabel"]
document_type: "kaggle"
competition: "Allen Institute"
status: Completed
summary: "Multi-label cell line classification under heavy class imbalance with focal loss"
---

# Allen Institute Cell Line Classification

Multi-label protein localization across cell images — extreme imbalance, 19 labels per image possible.

## Approach

- **Focal loss** over sigmoid outputs (multi-label, not softmax)
- Channel-wise image merging for the four microscopy channels
- Per-class threshold tuning post-training
- TTA over flips and rotations

## Results

Macro F1 0.68 — dominated by the rarest classes. Threshold tuning moved macro F1 more than any architecture change; per-class thresholds were essential.
