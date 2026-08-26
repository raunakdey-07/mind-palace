---
title: "Project: Local LLM Playground for Prompt Iteration"
date: 2024-06-12
tags: ["python", "llm", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/llm-playground"
summary: "Side-by-side prompt comparison tool over local Ollama models"
---

# Local LLM Playground

Prompt-iteration harness used to tune Mind Palace's grounding instructions.

## Features

- Multiple prompts evaluated against the same question set in parallel
- Side-by-side output diffing with per-run latency capture
- Model switching across locally installed Ollama models
- Golden-set scoring: expected-substring and abstention checks run automatically per experiment

## Finding

Instruction phrasing mattered more than model choice for abstention behavior: "answer only from context, say so if absent" outperformed longer policy paragraphs across every model tried.
