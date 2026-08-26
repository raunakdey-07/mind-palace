---
title: "Kaggle: Google Universal Image Embedding"
date: 2024-02-20
tags: ["kaggle", "metric-learning", "computer-vision"]
document_type: "kaggle"
competition: "Universal Image Embedding"
status: Completed
summary: "Unified image embeddings across product, landmark, and artwork retrieval domains"
---

# Universal Image Embeddings

One embedding space for retrieval across wildly different image domains — products, landmarks, artwork.

## Approach

- Domain-specific backbones sharing an embedding head
- **ArcFace** per domain with gradient balancing so no domain dominates
- Test-time ensembling over scales and crops
- Nearest-neighbor submission with per-domain similarity calibration

## Results

The universal-embedding tension was the lesson: shared-space quality lagged every single-domain model. Generalization across domains costs specialization within them.
