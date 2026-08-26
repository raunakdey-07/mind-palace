---
title: "Kaggle: Digit Recognizer CNN from Scratch"
date: 2022-10-12
tags: ["kaggle", "computer-vision", "beginner"]
document_type: "kaggle"
competition: "Digit Recognizer"
status: Completed
summary: "MNIST digit classification with a hand-built CNN — first computer vision competition"
---

# Digit Recognizer

MNIST handwritten digits — the entry point into computer vision.

## Model

- Three conv blocks (32, 64, 128 filters) with max pooling
- Dense head with dropout 0.5
- Data augmentation: rotation, shift, zoom

## Results

99.4% accuracy. The baseline that later made EfficientNet transfer learning feel like cheating.
