---
title: "Kaggle: LLMScienceExam QA with Retrieval"
date: 2024-02-25
tags: ["kaggle", "nlp", "rag", "llm"]
document_type: "kaggle"
competition: "LLM Science Exam"
status: Completed
summary: "Science multiple-choice QA using retrieval over Wikipedia plus a fine-tuned LLM"
---

# LLM Science Exam with Retrieval

Multiple-choice science questions answered by retrieving supporting Wikipedia passages — the closest Kaggle analogue to Mind Palace's architecture.

## Approach

- **Dense retrieval**: sentence-transformer embeddings over a Wikipedia chunk corpus, top-5 passage recall
- Answer selection: fine-tuned Longformer scoring each (context, question, answer) triple
- Prompt-based fallback with a local LLM for comparison
- Retrieval recall measured independently before end-to-end scoring

## Results

Accuracy 0.78 with retrieval vs 0.52 closed-book. The retrieval stage was the entire game: answer-ranking errors almost always traced back to missing evidence, not bad ranking among good passages.
