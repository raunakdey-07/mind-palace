---
title: "Kaggle: NLP Disaster Tweets Classification"
date: 2023-06-10
tags: ["kaggle", "nlp", "transformer"]
document_type: "kaggle"
competition: "Real or Not? NLP with Disaster Tweets"
status: Completed
summary: "Fine-tuned RoBERTa for disaster tweet classification with keyword handling"
---

# NLP Disaster Tweets Classification

Binary classification of whether tweets describe real disasters.

## Approach

- **RoBERTa-base** fine-tuning with a classification head
- Keyword and location field handling: missing-value indicators as extra features
- Adversarial validation to check train/test distribution shift
- 5-fold stratified ensemble with soft voting

## Results

F1 0.843 on the leaderboard. Most gains came from text cleaning (URLs, entities) rather than architecture.
