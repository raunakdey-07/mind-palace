---
title: "Project: Image Classifier for Plant Diseases"
date: 2023-12-01
tags: ["python", "computer-vision", "deep-learning"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/plant-disease"
summary: "CNN classifier detecting plant leaf diseases with Grad-CAM explanations"
---

# Plant Disease Image Classifier

Computer-vision project classifying 38 plant disease classes from leaf photographs (PlantVillage dataset).

## Approach

- Transfer learning from **EfficientNet-B0** ImageNet weights
- Heavy geometric + color augmentation to fight overfitting
- **Grad-CAM** heatmaps so predictions are explainable to non-ML users
- Mobile export via ONNX for on-device inference

## Results

96.2% test accuracy; Grad-CAM showed the model sometimes focused on background soil — fixed by center-crop preprocessing.
