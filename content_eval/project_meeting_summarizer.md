---
title: "Project: Meeting Notes Summarizer"
date: 2024-05-30
tags: ["python", "llm", "nlp"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/meeting-notes"
summary: "Transcript-to-minutes pipeline with action item extraction and speaker attribution"
---

# Meeting Notes Summarizer

Pipeline combining the transcription service with LLM summarization.

## Pipeline

- Audio → Whisper transcription with speaker diarization
- Transcript segmentation by topic-shift heuristics
- Per-segment summarization, then a synthesis pass for global minutes
- Action-item extraction with owner and deadline normalization

## Evaluation

Compared generated minutes against human notes on 20 meetings: action items had ~90% recall; narrative summaries rated well but occasionally merged distinct decisions — the topic segmentation was the bottleneck.
