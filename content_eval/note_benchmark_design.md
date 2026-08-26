---
title: "Note: Benchmark Design for Retrieval Systems"
date: 2024-06-30
tags: ["evaluation", "retrieval", "note"]
document_type: "note"
status: Complete
summary: "What makes a retrieval benchmark trustworthy: scale, difficulty, and independence"
---

# Benchmark Design for Retrieval Systems

Synthesis of everything learned building this repository's evaluation framework.

## Scale

Below ~50 documents, retrieval is trivially easy and all strategies score near-perfect — differences are noise. Meaningful strategy comparison needs enough distractors that ranking actually discriminates. Below that threshold, benchmarks are useful only as regression smoke tests.

## Difficulty Mix

A trustworthy set includes: exact lexical matches (floor), paraphrases (semantic value), terminology gaps (generalization), similar-title discrimination, section-buried answers, multi-document questions, and negative queries where the correct output is nothing.

## Independence

Labels come from reading documents, never from system output. Labels must be mechanically validated against corpus metadata. The benchmark must not be tuned after inspecting failures without holding out fresh queries — otherwise it becomes a leakage channel.
