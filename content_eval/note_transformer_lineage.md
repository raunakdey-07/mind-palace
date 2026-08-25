---
title: "Note: Transformer Models from BERT to LLMs"
date: 2024-02-10
tags: ["ml-fundamentals", "nlp", "transformer", "note"]
document_type: "note"
status: Complete
summary: "Evolution of transformer architectures from encoder models to instruction-tuned LLMs"
---

# Transformer Models: BERT to LLMs

Study note tracing the architecture lineage behind the NLP competitions and RAG work.

## Lineage

- **BERT**: bidirectional encoder, masked language modeling — the workhorse for classification and QA labeling
- **RoBERTa**: BERT with better training recipe; used for disaster tweets
- **DeBERTa**: disentangled attention; strongest encoder for Feedback Prize
- **GPT family**: decoder-only, autoregressive; foundation of modern LLMs
- **Instruction-tuned LLMs**: RLHF/DPO alignment enabling chat and RAG answering

## Practical Notes

Encoder models remain best for supervised text classification; decoder LLMs win for generation. Embedding models are typically encoder-based with pooling.
