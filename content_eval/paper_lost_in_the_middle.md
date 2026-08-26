---
title: "Paper Notes: Lost in the Middle How Language Models Use Long Contexts"
date: 2024-05-15
tags: ["paper", "llm", "rag"]
document_type: "paper"
status: Read
summary: "Liu et al. 2023 — LLMs attend poorly to information in the middle of long contexts"
---

# Paper: Lost in the Middle (Liu et al., 2023)

Directly relevant to Mind Palace's context budget and prompt layout.

## Core Idea

Language models reliably use information at the **beginning and end** of their context window; recall degrades substantially for evidence placed in the middle, regardless of model size.

## Key Findings

- U-shaped performance curve over context position
- More retrieved documents can *hurt* if relevance is not ordered
- Ordering the most relevant evidence first is a free improvement

## Relevance

Validates two Mind Palace design choices: the context budget drops lowest-ranked evidence (keeping prompts short), and chunks are inserted highest-ranked first. Both follow this paper's guidance.
