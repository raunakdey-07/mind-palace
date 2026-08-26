---
title: "Project: Recipe Ingredient Parser and Matcher"
date: 2024-04-14
tags: ["python", "nlp", "parsing"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/recipe-parser"
summary: "Parsing ingredient strings into quantity, unit, and item with fuzzy pantry matching"
---

# Recipe Ingredient Parser

Structured extraction from messy ingredient lines — a small parsing problem with real ambiguity.

## Pipeline

- Quantity/unit extraction via regex grammar ("2 1/2 cups", "500g", "a pinch")
- Item name normalization against a canonical ingredient taxonomy
- Fuzzy matching (rapidfuzz) for variants: "scallions" → "green onions"
- Pantry-matching mode: which recipes can be made from available items

## Evaluation

95% exact parse accuracy on 500 hand-labeled lines; the remaining 5% were genuinely ambiguous even for humans. The fuzzy matcher's recall mattered more than parse perfection downstream.
