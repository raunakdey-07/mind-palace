---
title: "Note: Vector Quantization for Embedding Storage"
date: 2024-06-05
tags: ["embeddings", "performance", "note"]
document_type: "note"
status: Complete
summary: "Product quantization and dimensionality reduction tradeoffs for vector indexes"
---

# Vector Quantization

Storage and memory options when embedding collections grow.

## Options

- **Float32 full precision**: baseline; 384-dim × 1M vectors ≈ 1.5 GB
- **Product quantization (PQ)**: subvector codebooks; ~10–20× compression, some recall loss
- **Scalar quantization**: int8; ~4× compression, minimal recall loss
- **Dimensionality reduction** (PCA/Matryoshka): model-dependent availability

## pgvector Context

pgvector supports half-precision and binary quantization natively. At Mind Palace's scale (thousands of vectors), quantization is unnecessary complexity — memory is not the constraint. Revisit only if the corpus reaches millions of chunks.
