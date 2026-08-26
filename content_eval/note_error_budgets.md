---
title: "Note: Error Budgets and SLOs for Internal Services"
date: 2024-06-08
tags: ["devops", "reliability", "note"]
document_type: "note"
status: Complete
summary: "Setting service level objectives that balance reliability against change velocity"
---

# Error Budgets and SLOs

Reliability targets as an engineering tool rather than a promise.

## Structure

- **SLI**: the measured quantity (p95 latency, success rate) — must be user-meaningful
- **SLO**: the target (99% of searches under 100 ms)
- **Error budget**: allowed failure; spent by incidents *and* by risky changes

## The Tradeoff

A full error budget means slow down changes; a healthy budget licenses experimentation. This formalizes the reranking decision: its latency cost would consume budget on every query, so it must justify itself in quality — which it has not yet.

## For Personal Projects

SLOs still help at small scale by making "good enough" explicit, preventing both gold-plating and silent rot.
