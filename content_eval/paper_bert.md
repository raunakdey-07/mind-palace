---
title: "Paper Notes: BERT Pre-training of Deep Bidirectional Transformers"
date: 2023-10-15
tags: ["paper", "nlp", "transformer"]
document_type: "paper"
status: Read
summary: "Devlin et al. 2018 — masked language model pretraining and its transfer dominance"
---

# Paper: BERT (Devlin et al., 2018)

The encoder-side foundation referenced throughout the transformer lineage note.

## Core Idea

Pretrain a deep bidirectional transformer with two objectives — **masked language modeling** and next-sentence prediction — then fine-tune the whole model per task.

## Key Findings

- Pretraining on unlabeled text transfers to nearly every NLP task with one output layer
- Bidirectional context matters: masked objectives beat autoregressive ones for understanding tasks
- Model size scaling already visible: BERT-large over BERT-base across the board

## Relevance

Every text encoder used in the Kaggle NLP work (RoBERTa, DeBERTa, XLM-R) is a BERT descendant, and Sentence-BERT is literally BERT adapted for embeddings.
