---
title: "Note: Class Imbalance Techniques Compared"
date: 2023-09-18
tags: ["ml-fundamentals", "class-imbalance", "note"]
document_type: "note"
status: Complete
summary: "Weighted sampling, focal loss, SMOTE, and threshold tuning for imbalanced datasets"
---

# Class Imbalance Techniques Compared

Companion note to the BirdCLEF and disaster-tweets work; written after hitting imbalance in three separate projects.

## Techniques

- **Weighted sampling**: oversample rare classes per batch — used in BirdCLEF 2023
- **Focal loss**: down-weights easy examples; replaced sampling in BirdCLEF 2024
- **SMOTE**: synthetic minority oversampling; works on tabular, dubious on images
- **Class weights** in the loss: cheapest first try
- **Threshold tuning**: post-hoc, free, and often the biggest F1 win

## Guidance

Start with class weights and threshold tuning. Escalate to focal loss only when the positive class is extremely rare. Never evaluate imbalance handling on accuracy.
