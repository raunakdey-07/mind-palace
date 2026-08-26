---
title: "Note: Text Preprocessing for Modern NLP"
date: 2023-11-05
tags: ["nlp", "ml-fundamentals", "note"]
document_type: "note"
status: Complete
summary: "What preprocessing transformer models still need — and what they no longer need"
---

# Text Preprocessing for Modern NLP

Counterpoint to classical NLP pipelines: transformers changed the rules.

## No Longer Needed

- Stemming/lemmatization (subword tokenizers handle morphology)
- Stopword removal (attention learns importance)
- Lowercasing for cased models — harmful if the model is case-sensitive

## Still Needed

- URL/email/mention normalization in social text
- HTML entity and encoding repair
- Whitespace canonicalization
- Deduplication — near-duplicates waste training and pollute evaluation

## For Retrieval Specifically

Preprocessing at query time must match preprocessing at index time. Asymmetric pipelines are a silent retrieval-quality killer.
