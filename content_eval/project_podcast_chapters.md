---
title: "Project: Podcast Chapter Marker Generator"
date: 2024-06-20
tags: ["python", "audio", "nlp"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/podcast-chapters"
summary: "Automatic podcast chaptering from transcript topic shifts and audio features"
---

# Podcast Chapter Marker Generator

Topic segmentation for long-form audio — the production application of the meeting-summarizer segmentation work.

## Pipeline

- Whisper transcription with word-level timestamps
- Topic-shift detection over transcript embedding windows
- Boundary refinement using pause lengths and music cues from the audio
- Chapter titles generated per segment with an LLM

## Status

Boundary detection precision is good on strongly-themed episodes; conversational shows that circle topics remain hard — the same topic-drift problem the meeting summarizer hit.
