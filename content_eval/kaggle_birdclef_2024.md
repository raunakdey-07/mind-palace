---
title: "BirdCLEF 2024: Soundscape Analysis"
date: 2024-05-20
tags: ["kaggle", "audio-classification", "birdclef"]
document_type: "kaggle"
competition: "BirdCLEF 2024"
status: Completed
git_repo: "https://github.com/raunakdey-07/birdclef-2024"
summary: "Soundscape species detection with pseudo-labeling and focal loss"
---

# BirdCLEF 2024: Soundscape Analysis

Follow-up to BirdCLEF 2023 targeting soundscape-level detection rather than clip classification.

## Approach

- **Pseudo-labeling** on unlabeled soundscapes to expand training data
- **Focal loss** instead of cross-entropy for extreme class imbalance
- **Secondary classifier** filtering false positives before scoring
- 5-fold **ensemble** with rank averaging

## Results

- Top 8% public leaderboard, private gold medal range
- Inference under 90 minutes for the full test set

## Differences from 2023

Moved from mel-spectrogram CNNs to fine-tuned audio transformers; class imbalance handled by focal loss rather than weighted sampling.
