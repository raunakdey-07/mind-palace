---
title: "Project: Expense Receipt OCR Pipeline"
date: 2024-02-28
tags: ["python", "ocr", "finance"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/receipt-ocr"
summary: "Receipt photo to structured expense record with field-level extraction confidence"
---

# Receipt OCR Pipeline

Photograph → structured expense entry, feeding the finance dashboard.

## Pipeline

- Image preprocessing: perspective correction, contrast normalization
- **Tesseract** with receipt-tuned configuration; cloud OCR comparison baseline
- Field extraction (merchant, total, date) via layout heuristics + regex
- Confidence scores per field; low-confidence fields routed to manual review

## Results

Total extraction accuracy 93%; merchant names were hardest due to stylized logos. The confidence-gated review flow was what made the system trustworthy enough for daily use.
