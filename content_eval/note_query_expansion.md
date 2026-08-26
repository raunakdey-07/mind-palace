---
title: "Note: Query Understanding and Expansion"
date: 2024-06-16
tags: ["retrieval", "search", "note"]
document_type: "note"
status: Complete
summary: "Query rewriting, expansion, and multi-query retrieval for vocabulary mismatch"
---

# Query Understanding and Expansion

Retrieval-side remedies for the terminology-mismatch failures Mind Palace's benchmark exposes.

## Techniques

- **Multi-query retrieval**: an LLM rewrites the query several ways; union the result sets
- **HyDE**: generate a hypothetical answer document and search with that instead
- **Synonym/ontology expansion**: controlled vocabularies where domains have them
- **Step-back prompting**: generalize the question before retrieving

## Tradeoffs

Every expansion adds LLM latency to the critical path — the same cost class as reranking. Multi-query union also changes fusion semantics: a document retrieved by two query variants deserves more weight than one.

## When It Pays

Vocabulary-mismatch misses (the "Altman Z score" class) are exactly what expansion fixes. Lexical misses on rare identifiers are not — expansion can even hurt those by diluting exact terms.
