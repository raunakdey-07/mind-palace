---
title: "Project: Knowledge Graph Builder from Wikipedia"
date: 2024-06-20
tags: ["python", "nlp", "knowledge-graph", "neo4j"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/kg-builder"
summary: "Entity-relation extraction pipeline building a queryable knowledge graph in Neo4j"
---

# Knowledge Graph Builder

Experimental pipeline extracting entities and relations from Wikipedia articles into a **Neo4j** graph.

## Pipeline

- NER with spaCy transformer model
- Relation extraction via dependency-path patterns and an LLM fallback
- Entity resolution / deduplication with embedding similarity
- Graph writes to **Neo4j** with MERGE-based idempotent Cypher

## Motivation

Explores whether explicit entity relations enable multi-hop questions that flat retrieval cannot answer — directly relevant to the Mind Palace graph question.
