---
title: "Kaggle: Riiid Answer Correctness Prediction"
date: 2023-05-25
tags: ["kaggle", "education", "time-series", "tabular"]
document_type: "kaggle"
competition: "Riiid"
status: Completed
summary: "Student knowledge tracing with question embeddings and exponential history features"
---

# Riiid Knowledge Tracing

Predicting whether students answer their next question correctly — education meets time series.

## Approach

- Exponential decay features over each student's answer history
- Question content embeddings from question metadata
- **SAINT-style transformer** compared against GBDT; GBDT won on this feature set
- Rolling-window validation matching the competition's live update format

## Results

AUC 0.78. History features dominated; the transformer only caught up when given raw interaction sequences rather than engineered summaries.
