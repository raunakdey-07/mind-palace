---
title: "Kaggle: US Patent Phrase Matching"
date: 2023-11-10
tags: ["kaggle", "nlp", "classification"]
document_type: "kaggle"
competition: "US Patent Phrase Matching"
status: Completed
summary: "Phrase similarity scoring with patent-domain-pretrained transformers and CPC context"
---

# US Patent Phrase Matching

Score anchor/context/phrase similarity for patent classification — domain-specific language matters.

## Approach

- **PatentBERT**: BERT further pretrained on patent corpus before task fine-tuning
- CPC classification code as an additional input segment
- Pairwise (context, phrase) encoding rather than independent encodings
- Fold ensembling across stratified anchors

## Results

Pearson 0.86. Domain-adaptive pretraining beat bigger generic models — the strongest evidence in my experience for domain vocabulary mattering more than scale.
