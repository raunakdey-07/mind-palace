---
title: "Project: Interview Question Generator"
date: 2024-04-20
tags: ["python", "llm", "rag"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/interview-gen"
summary: "Generating technical interview questions from project documentation with difficulty control"
---

# Interview Question Generator

The standalone prototype of Mind Palace's interview intent.

## Design

- Source documents chunked by heading; questions generated per section for coverage
- Difficulty conditioning via prompt (recall / application / deep-dive)
- Answer outlines generated alongside questions for self-assessment
- Deduplication by question embedding similarity

## Evaluation

Generated sets reviewed against a rubric: specificity, answerability from the source, and difficulty calibration. Prompt-only difficulty control was ~80% reliable; explicit difficulty examples in the prompt improved it.
