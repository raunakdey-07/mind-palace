---
title: "Kaggle: Cassava Leaf Disease Classification"
date: 2023-09-14
tags: ["kaggle", "computer-vision", "classification"]
document_type: "kaggle"
competition: "Cassava"
status: Completed
summary: "Crop disease classification with label noise handling and EfficientNet ensembles"
---

# Cassava Leaf Disease Classification

Agricultural vision task with substantial **label noise** — farmer-collected labels are imperfect.

## Approach

- **EfficientNet-B3** family, 5-fold stratified
- Label-noise handling: train/test disagreement filtering and soft labels from model consensus
- Progressive resizing (288 → 384px) in later epochs
- TTA over crops and flips

## Results

Accuracy 0.90 private. The consensus-based relabeling of suspicious training images was worth more than any architecture change — same lesson as Grad-CAM gave for the plant disease project.
