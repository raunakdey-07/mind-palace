---
title: "Kaggle: Histopathologic Cancer Detection"
date: 2023-03-22
tags: ["kaggle", "computer-vision", "healthcare"]
document_type: "kaggle"
competition: "Histopathologic Cancer"
status: Completed
summary: "Patch-level cancer detection with weakly-supervised slide aggregation"
---

# Histopathologic Cancer Detection

Classify 96×96 patches of lymph node sections — a medical imaging competition with weak labels.

## Approach

- **ResNet/DenseNet** family comparison; DenseNet121 won
- Center-region masking: only central 32×32 pixels determine the label, so crops focus there
- Slide-level aggregation from patch predictions for interpretability
- Heavy augmentation; stain-normalization experiments gave marginal gains

## Results

AUC 0.966. The center-masking insight — respect how the label was constructed — applied directly to later weak-label problems like Cassava.
