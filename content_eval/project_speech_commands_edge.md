---
title: "Project: Speech Command Recognition on Edge Devices"
date: 2023-11-28
tags: ["python", "audio", "deep-learning", "edge"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/speech-commands"
summary: "Keyword-spotting model quantized for microcontroller deployment"
---

# Speech Command Recognition

Audio-domain project complementing the environmental-sound work: recognizing spoken keywords, not bird calls.

## Approach

- 1D convolutions over raw waveform plus mel-spectrogram baseline comparison
- **Quantization-aware training** for int8 deployment
- Target device: ESP32 with 2MB flash budget

## Results

94% accuracy on 12 commands at 41KB model size. Quantization cost only 0.8% accuracy; mel features beat raw-waveform convs on noise robustness.
