---
title: "Deployment Guide"
date: 2024-07-02
tags: ["ops"]
document_type: "note"
summary: "How the example product is deployed"
---

# Deployment

The product deploys as a single container with a PostgreSQL sidecar.

## Rollouts

Rolling updates drain connections before replacement; rollbacks are instant via image tag revert.
