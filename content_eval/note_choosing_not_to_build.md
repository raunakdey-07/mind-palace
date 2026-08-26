---
title: "Note: Choosing What Not to Build"
date: 2024-06-28
tags: ["process", "architecture", "note"]
document_type: "note"
status: Complete
summary: "Negative decisions as engineering artifacts: recording what was rejected and why"
---

# Choosing What Not to Build

The most valuable sections in any architecture document are the rejected options.

## Why Record Rejections

Without a record, rejected ideas resurface every few months and each review re-litigates them from scratch. A written decision with its evidence converts a recurring debate into a citation.

## What the Record Needs

- The option and what it promised
- The evidence examined (measurements, not vibes)
- The condition under which the decision should be revisited
- The date — evidence expires as corpora and workloads grow

## Examples from Practice

"Neo4j: deferred, no multi-hop workload demonstrated; revisit if queries of form project→technology→concept appear." This sentence prevents both premature adoption and endless reconsideration.
