---
title: "Kaggle: Feedback Prize Evaluating Student Summaries"
date: 2024-03-18
tags: ["kaggle", "nlp", "regression"]
document_type: "kaggle"
competition: "Feedback Prize 4"
status: Completed
summary: "Student summary scoring against source texts with content and wording models"
---

# Feedback Prize: Evaluating Student Summaries

Score student summaries on **content** (what was included) and **wording** (paraphrase quality) against the source text.

## Approach

- Siamese encoding of (source text, summary) pairs with DeBERTa
- Separate heads per measure; content benefited from word-overlap features, wording did not
- Long-source truncation strategy: keep first + last segments
- Fold ensembling with rank averaging

## Results

MSE 0.45 private. Content scoring was largely lexical overlap; wording required genuine paraphrase understanding — the two measures needed different models in spirit.
