---
title: "Kaggle: Learning Agency Diagram Segmentation"
date: 2024-03-25
tags: ["kaggle", "computer-vision", "segmentation"]
document_type: "kaggle"
competition: "Learning Agency"
status: Completed
summary: "Handwritten essay diagram segmentation with YOLO and U-Net comparison"
---

# Essay Diagram Segmentation

Segment hand-drawn diagrams within student essays — dense prediction over noisy scans.

## Approach

- **YOLOv8** instance segmentation compared against a U-Net semantic baseline
- Anchor-free detection handled variable diagram counts per page
- Scan artifacts (lined paper, punch holes) as explicit negative classes
- Ensemble of two image scales

## Results

Dice 0.74. The explicit artifact classes prevented the models from wasting capacity on systematic false positives — data hygiene expressed as architecture.
