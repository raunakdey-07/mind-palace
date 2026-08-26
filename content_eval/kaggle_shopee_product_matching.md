---
title: "Kaggle: Shopee Product Matching"
date: 2023-06-08
tags: ["kaggle", "metric-learning", "computer-vision", "nlp"]
document_type: "kaggle"
competition: "Shopee"
status: Completed
summary: "Product deduplication fusing image and text embeddings with metric learning"
---

# Shopee Product Matching

Find duplicate product listings across image and title — multimodal metric learning.

## Approach

- **ArcFace** heads on both an EfficientNet image branch and a text transformer branch
- Combined similarity: weighted sum of image and text cosine similarities
- Nearest-neighbor search with a per-group threshold calibration
- Post-processing: transitive closure over matched pairs to form listing groups

## Results

F1 0.82. Text dominated for titled products; images rescued listings with generic titles. The fusion weighting was the key hyperparameter — and its instability across folds was the practical argument for rank-based fusion in later projects.
