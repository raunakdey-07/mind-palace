---
title: "Project: Audio Transcription Service with Whisper"
date: 2024-03-05
tags: ["python", "audio", "deep-learning", "fastapi"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/whisper-service"
summary: "FastAPI wrapper around OpenAI Whisper for batch audio transcription"
---

# Audio Transcription Service

Production-style wrapper serving **Whisper** transcription over HTTP — audio domain, but speech not environmental sound.

## Design

- **FastAPI** endpoint accepting multipart uploads
- Background task queue for long files
- Chunked VAD-based segmentation for files over 30 seconds
- Output formats: plain text, SRT, verbose JSON with timestamps

## Operations

Model loaded once at startup (singleton); GPU optional, CPU fallback tested. Handles podcast-length files via streaming chunk assembly.
