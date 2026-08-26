---
title: "Kaggle: Stanford Ribonucleic Acid Structure Prediction"
date: 2024-06-05
tags: ["kaggle", "deep-learning", "sequence"]
document_type: "kaggle"
competition: "Stanford RNA"
status: Completed
summary: "RNA 3D structure prediction with sequence models and geometric post-processing"
---

# RNA Structure Prediction

Predicting 3D RNA backbone coordinates from sequence — hard science, small data.

## Approach

- Sequence encoder with relative positional embeddings
- Pairwise distance prediction rather than direct coordinate regression
- Geometric refinement enforcing bond-length and angle constraints post-hoc
- Cross-validation by RNA family to test generalization to unseen folds

## Results

The constraint-based post-processing improved physical plausibility substantially even where raw coordinate accuracy was limited. Domain constraints as a corrective layer beat hoping the network learns physics implicitly.
