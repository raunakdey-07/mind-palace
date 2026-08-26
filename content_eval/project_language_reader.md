---
title: "Project: Language Learning Reader with Inline Translations"
date: 2024-05-06
tags: ["python", "nlp", "streamlit"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/language-reader"
summary: "Interactive reading app with click-to-translate and vocabulary tracking integration"
---

# Language Learning Reader

Interactive foreign-language reading — connecting to the SRS scheduler for vocabulary.

## Design

- Tokenization and lemma lookup against a dictionary database
- Click-to-translate with context-aware disambiguation (part of speech first)
- Known-word tracking exported to the SRS scheduler as new cards
- Text difficulty scoring to suggest appropriately-leveled reading

## Status

Core reading loop works; difficulty scoring is calibrated only for one language pair so far. The SRS integration is what elevates it above a dictionary with extra steps.
