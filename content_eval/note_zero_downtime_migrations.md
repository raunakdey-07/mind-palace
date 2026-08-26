---
title: "Note: Zero-Downtime Schema Migrations"
date: 2024-05-10
tags: ["database", "devops", "note"]
document_type: "note"
status: Complete
summary: "Expand-contract migration patterns and avoiding locks in live databases"
---

# Zero-Downtime Migrations

Schema-change discipline for databases that must stay up.

## Expand-Contract Pattern

1. **Expand**: add new column/table alongside the old; deploy code that writes both
2. **Migrate**: backfill existing rows in batches
3. **Contract**: remove old columns after code stops reading them

Each step deploys independently; no step requires downtime.

## Lock Hazards

Adding a NOT NULL column or changing a type takes ACCESS EXCLUSIVE locks — on large tables this blocks everything. Use nullable-then-constrain, and keep transactions short around DDL.

## Mind Palace Application

The single-migration schema is safe because it runs before any traffic exists. The expand-contract pattern becomes mandatory the moment migrations run against a database serving queries.
