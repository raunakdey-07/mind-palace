---
title: "Paper Notes: Language Models are Few-Shot Learners GPT-3"
date: 2024-01-20
tags: ["paper", "llm", "transformer"]
document_type: "paper"
status: Read
summary: "Brown et al. 2020 — scale enables in-context learning without fine-tuning"
---

# Paper: GPT-3 (Brown et al., 2020)

The decoder-side milestone of the transformer lineage.

## Core Idea

Scale a decoder-only transformer to 175B parameters and few-shot prompting emerges: the model performs tasks from instructions and examples in the prompt, with no gradient updates.

## Key Findings

- In-context learning quality scales smoothly with model size
- Task performance depends heavily on prompt formulation — an engineering surface, not just a capability
- Few-shot beats zero-shot, which beats traditional pretrain-finetune at insufficient fine-tuning data

## Relevance

Explains why Mind Palace's generation layer is prompt-engineered rather than fine-tuned, and why grounding instructions in the prompt have measurable effect.
