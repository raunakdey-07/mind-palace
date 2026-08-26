---
title: "Kaggle: Airbnb Kaggle Competition Destination Prediction"
date: 2022-12-15
tags: ["kaggle", "classification", "beginner"]
document_type: "kaggle"
competition: "Airbnb New User Bookings"
status: Completed
summary: "Destination country prediction with class imbalance and probabilistic ranking metrics"
---

# Airbnb Destination Prediction

Predicting booking destination country — heavily imbalanced multi-class with NDCG@5 scoring.

## Approach

- Gradient boosting with class-prior correction for the dominant US class
- Probabilistic outputs ranked rather than argmax-classified (the metric rewards ordering)
- Session-level features from the auxiliary data file

## Results

NDCG@5 0.88. First exposure to ranking-style metrics rewarding calibrated orderings over top-1 accuracy — conceptually a precursor to caring about MRR in retrieval.
