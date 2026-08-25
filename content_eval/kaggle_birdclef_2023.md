---
title: "BirdCLEF 2023: Audio Classification"
date: 2023-07-15
tags: ["kaggle", "audio-classification", "ensemble"]
document_type: "kaggle"
competition: "BirdCLEF 2023"
status: Completed
git_repo: "https://github.com/raunakdey-07/birdclef"
summary: "Audio classification using CNN + transformers for bird sound identification"
---

# BirdCLEF 2023: Audio Classification

This project tackled the BirdCLEF 2023 Kaggle competition, focusing on identifying bird species from audio recordings.

## Approach

The solution combined a CNN backbone with transformer-based attention layers to capture both local spectral patterns and long-range temporal dependencies in bird calls.

### Key Techniques

- **Mel spectrogram augmentation** to increase training diversity
- **Weighted sampling** to handle class imbalance across 264 bird species
- **Ensemble of 5 models** with weighted averaging for final predictions
- **Test-time augmentation (TTA)** with horizontal flips and pitch shifts

## Results

- Achieved **top 15%** on the public leaderboard
- Cross-validation F1 of 0.87 across species folds

## Lessons Learned

Class imbalance dominated the problem: rare species had fewer than 20 training samples. Weighted sampling and heavy augmentation mattered more than architecture changes.
