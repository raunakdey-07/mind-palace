---
title: "Project: PDF Extraction Pipeline for Research Papers"
date: 2024-05-25
tags: ["python", "nlp", "parsing"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/pdf-extraction"
summary: "Structured extraction of sections, tables, and references from research PDFs"
---

# PDF Extraction Pipeline

Feeding the arXiv search project with full-text rather than abstracts only.

## Design

- Layout-aware parsing (GROBID) for section boundaries
- Table extraction with type inference
- Reference list parsing into citation graph edges
- Fallback regex pipeline for scanned documents via OCR

## Status

Section extraction accuracy ~92% on a 200-paper sample; tables remain hard. The citation graph output is what would eventually enable paper→technique→project relationship queries.
