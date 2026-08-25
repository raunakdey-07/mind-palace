---
title: "Note: Prompt Engineering for Grounded Answers"
date: 2024-05-08
tags: ["llm", "rag", "prompting", "note"]
document_type: "note"
status: Complete
summary: "Instruction patterns that reduce hallucination in retrieval-augmented answering"
---

# Prompt Engineering for Grounded Answers

Companion to the RAG patterns note, focused on the generation side.

## Instruction Patterns

- State the constraint explicitly: answer *only* from provided context
- Require explicit abstention: instruct the model to say when the answer is absent
- Ask for per-claim source citation to make checking possible
- Delimit evidence with unambiguous separators so chunks cannot bleed together
- Low temperature for factual answering; reserve higher temperature for ideation

## Limits

Prompting reduces hallucination frequency but cannot guarantee it. Verification — checking claims against retrieved text — is a separate system component, not a prompt tweak.
