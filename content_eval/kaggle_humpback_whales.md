---
title: "Kaggle: Humpback Whale Identification"
date: 2023-07-20
tags: ["kaggle", "computer-vision", "metric-learning"]
document_type: "kaggle"
competition: "Humpback Whale"
status: Completed
summary: "Whale tail individual identification with metric learning and ArcFace loss"
---

# Humpback Whale Identification

Individual whale recognition from tail fluke photographs — a metric-learning problem, not classification.

## Approach

- **ArcFace** margin loss over EfficientNet embeddings; softmax classes are unstable when individuals have single images
- Novel-individual handling: new whales matched by nearest-neighbor distance rather than class head
- Test-time augmentation over crops and flips
- 5-fold embedding ensembling

## Results

MAP@5 0.87. The shift from classification to metric learning was the entire solution — per-class heads cannot generalize to unseen individuals.
