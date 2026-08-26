---
title: "Kaggle: Oxford-IIIT Pet Breeds and Species"
date: 2023-05-02
tags: ["kaggle", "computer-vision", "classification"]
document_type: "kaggle"
competition: "Oxford-IIIT Pets"
status: Completed
summary: "Fine-grained pet breed classification with progressive resizing and label smoothing"
---

# Oxford-IIIT Pet Classification

Fine-grained breed classification — 37 breeds where several are visually near-identical.

## Approach

- Progressive resizing: 224 → 320 → 448 pixels across training phases
- **Label smoothing** (0.1) for the visually confusable breed pairs
- Test-time augmentation with multi-crop averaging
- Confusion-matrix-driven error analysis guiding augmentation choices

## Results

Accuracy 94.6%. The confusion matrix review was the highest-value activity: it showed specific breed pairs (e.g., two similar terriers) accounting for most errors, which generic improvements would never have fixed.
