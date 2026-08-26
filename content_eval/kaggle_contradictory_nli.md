---
title: "Kaggle: Contradictory My Dear Watson NLI"
date: 2023-06-25
tags: ["kaggle", "nlp", "classification", "multilingual"]
document_type: "kaggle"
competition: "Contradictory My Dear Watson"
status: Completed
summary: "Multilingual natural-language inference with XLM-RoBERTa"
---

# Multilingual Natural Language Inference

Premise/hypothesis entailment classification across 15 languages.

## Approach

- **XLM-RoBERTa** fine-tuning; language-agnostic subword vocabulary
- Premise-hypothesis pair encoding with separator tokens
- Language-balanced stratification in CV folds

## Results

Accuracy 0.94. Low-resource languages lagged high-resource ones by ~4 points despite shared weights — capacity, not pretraining coverage, was the bottleneck.
