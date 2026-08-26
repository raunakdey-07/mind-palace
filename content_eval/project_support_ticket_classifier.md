---
title: "Project: Multilingual Text Classifier for Support Tickets"
date: 2024-01-30
tags: ["python", "nlp", "classification"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/support-classifier"
summary: "Routing support tickets across languages with XLM-R and active learning"
---

# Multilingual Support Ticket Classifier

Ticket routing — production deployment of the multilingual NLI experience.

## Approach

- **XLM-RoBERTa** classifier over 12 languages, one shared model
- Active-learning loop: low-confidence predictions routed to human review feed back as labels
- Class imbalance across categories handled with focal loss
- Confidence calibration (temperature scaling) so the review threshold is meaningful

## Results

F1 0.89 macro; the active-learning loop cut labeling effort by ~60% versus random sampling. Calibration mattered more than raw accuracy because thresholds drive routing.
