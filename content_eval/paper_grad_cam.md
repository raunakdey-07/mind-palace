---
title: "Paper Notes: Grad-CAM Visual Explanations from Deep Networks"
date: 2023-12-20
tags: ["paper", "computer-vision", "explainability"]
document_type: "paper"
status: Read
summary: "Selvaraju et al. 2017 — gradient-weighted class activation mapping for CNN explanations"
---

# Paper: Grad-CAM (Selvaraju et al., 2017)

The explainability technique used in the plant-disease classifier.

## Core Idea

Weight the last convolutional layer's feature maps by the gradient of the target class score, then ReLU-sum: a coarse heatmap of image regions supporting the prediction.

## Key Findings

- Applies to CNNs, and with guidance to vision transformers
- Faithful enough to catch shortcut learning — in our case, soil-background focus
- Requires no architecture change or retraining

## Relevance

Demonstrates why explanation tooling belongs in applied ML pipelines: it diagnoses data problems that accuracy metrics hide.
