---
title: "Kaggle: NOAA Fisheries Passaic River Detection"
date: 2024-04-15
tags: ["kaggle", "computer-vision", "detection"]
document_type: "kaggle"
competition: "NOAA Fisheries"
status: Completed
summary: "Underwater video fish detection with frame differencing and tracking-assisted labels"
---

# Underwater Fish Detection

Detecting fish in murky underwater video for fisheries monitoring.

## Approach

- Frame differencing to highlight motion against static backgrounds
- **YOLO** detection on sampled frames; tracking (ByteTrack) to consolidate per-fish counts
- Pseudo-labels from tracked detections bootstrapped training data
- Turbidity augmentation since water clarity varied wildly

## Results

The tracking-based pseudo-labeling doubled usable training data. Count accuracy mattered more than per-frame detection — defining the right target was half the work.
