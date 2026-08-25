---
title: "Paper Notes: Attention Is All You Need"
date: 2023-06-15
tags: ["paper", "transformer", "deep-learning"]
document_type: "paper"
status: Read
summary: "Vaswani et al. 2017 — the original transformer architecture replacing recurrence with attention"
---

# Paper: Attention Is All You Need (Vaswani et al., 2017)

Reading notes on the paper that underpins every transformer model in the lineage note.

## Core Idea

Replace recurrence entirely with **scaled dot-product self-attention**: every token attends to every other token in parallel, enabling full utilization of modern accelerators.

## Key Components

- Multi-head attention: multiple attention subspace projections per layer
- Positional encodings: sinusoids inject order information lost by permutation invariance
- Residual connections + layer norm around each sub-layer

## Why It Matters Here

Encoder stacks became BERT; decoder stacks became GPT. The classification heads used in the tweet/QUEST competitions and the attention layers used in BirdCLEF are direct descendants.
