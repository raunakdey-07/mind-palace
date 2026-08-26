---
title: "Note: Documentation Testing and Link Rot"
date: 2024-06-14
tags: ["documentation", "devops", "note"]
document_type: "note"
status: Complete
summary: "Mechanical checks that keep documentation truthful: command tests, link checks, drift detection"
---

# Documentation Testing

Documentation fails silently; only mechanical checks catch it.

## Checks That Work

- **Command extraction**: every fenced shell block in the README should be parseable and, where safe, executable in CI
- **Path validation**: referenced files and directories must exist
- **Label validation**: benchmark labels checked against corpus metadata (the failure this repository actually hit)
- **Drift detection**: documented defaults compared against code constants

## Culture

Documentation bugs should be filed and fixed like code bugs. A README command that fails is a defect with a reproduction case — treat it with the same severity as a test failure.
