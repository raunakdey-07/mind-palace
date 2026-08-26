---
title: "Kaggle: PetFinder Pawpularity Score"
date: 2023-10-02
tags: ["kaggle", "computer-vision", "regression"]
document_type: "kaggle"
competition: "PetFinder"
status: Completed
summary: "Photo engagement regression blending vision features with metadata tabular inputs"
---

# PetFinder Pawpularity

Predicting photo cuteness engagement — vision plus metadata fusion.

## Approach

- **Swin Transformer** image backbone
- Metadata branch (subject count, position, face/eye visibility flags) fused before the head
- Binary-auxiliary heads on the metadata flags as regularizers
- GRU over bounding-box coordinates considered and rejected as overkill

## Results

RMSE 17.2. The metadata flags carried real signal; end-to-end fusion beat late averaging by 0.4 RMSE.
