---
title: "Kaggle: Stable Diffusion Image to Prompt"
date: 2023-11-28
tags: ["kaggle", "diffusion", "computer-vision", "nlp"]
document_type: "kaggle"
competition: "Stable Diffusion"
status: Completed
summary: "Recovering generation prompts from diffusion-generated images with CLIP retrieval"
---

# Stable Diffusion Image-to-Prompt

Given a diffusion-generated image, recover the text prompt that produced it.

## Approach

- **CLIP** embeddings over generated images; nearest-neighbor retrieval from a prompt corpus
- Per-genre fine-tuned regression heads for prompt attributes
- Ensemble of retrieval candidates with token-level scoring
- Vocabulary analysis of the prompt distribution guided candidate filtering

## Results

The task was fundamentally retrieval-limited: if the true prompt's near-neighbors were not in the corpus, no model could recover them. A clean demonstration that retrieval coverage bounds generation quality.
