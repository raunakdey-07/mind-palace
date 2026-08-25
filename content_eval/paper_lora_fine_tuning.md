---
title: "Paper Notes: LoRA Low-Rank Adaptation of LLMs"
date: 2024-03-12
tags: ["paper", "fine-tuning", "llm"]
document_type: "paper"
status: Reading
summary: "Hu et al. 2021 — parameter-efficient fine-tuning via low-rank weight updates"
---

# Paper: LoRA (Hu et al., 2021)

Reading toward future Mind Palace fine-tuning experiments.

## Core Idea

Freeze pretrained weights; learn rank-decomposed update matrices `BA` where the update `ΔW = BA` has rank r ≪ d. Only millions of parameters train instead of billions.

## Key Findings

- Matches full fine-tuning on many tasks at a fraction of memory
- Adapter modules can be swapped per task from one base model
- No inference latency penalty when adapters are merged into weights

## Relevance

Candidate approach if domain-specific retrieval reranking or answer verification is ever fine-tuned; also standard for local LLM customization via Ollama tooling.
