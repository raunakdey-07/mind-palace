---
title: "Kaggle: Image Segmentation for Ship Detection"
date: 2023-03-30
tags: ["kaggle", "computer-vision", "segmentation"]
document_type: "kaggle"
competition: "Airbus Ship Detection"
status: Completed
summary: "U-Net segmentation with RLE encoding for satellite ship masks"
---

# Ship Detection Segmentation

Satellite imagery segmentation — computer vision, but dense prediction rather than classification.

## Approach

- **U-Net** with ResNet34 encoder, Dice + BCE loss
- Run-length encoding (RLE) submission format handling
- Empty-image classifier upstream to skip false-positive segments
- Test-time augmentation with flips

## Results

Dice 0.81; the upstream no-ship filter mattered more than the segmentation network itself.
