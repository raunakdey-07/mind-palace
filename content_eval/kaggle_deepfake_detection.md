---
title: "Kaggle: Deepfake Detection Challenge"
date: 2023-04-18
tags: ["kaggle", "computer-vision", "video"]
document_type: "kaggle"
competition: "Deepfake Detection"
status: Completed
summary: "Video deepfake detection with frame sampling and face-focused pipelines"
---

# Deepfake Detection Challenge

Video-level fake detection — first video-domain competition.

## Approach

- Face detection and tracking per frame; only face regions classified
- Frame sampling strategy: uncertainty-weighted selection over uniform
- **EfficientNet** per-frame scores aggregated by top-k pooling
- Temporal-consistency features explored; marginal contribution

## Results

Log-loss 0.29. The pipeline around the model (face extraction, frame selection) mattered more than the classifier itself — a recurring theme in applied ML.
