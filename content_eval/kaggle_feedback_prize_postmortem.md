---
title: "Kaggle: Feedback Prize English Language Learning Revisit"
date: 2024-07-08
tags: ["kaggle", "nlp", "postmortem"]
document_type: "kaggle"
competition: "Feedback Prize 3"
status: Completed
summary: "Post-competition analysis of what the winning solutions did differently"
---

# Feedback Prize Postmortem

Reading winning solutions after the fact — the highest-value learning activity in Kaggle.

## What Winners Did Differently

- Longer context handling via chunked inference with overlap merging, not truncation
- Per-measure model selection rather than one shared backbone
- Pseudo-labeling on the unlabelled test distribution

## My Gap

I truncated long essays; winners merged overlapping window predictions. A data-handling detail worth more than every architecture change I tried — the same class of lesson as the RRF column-mapping bug.
