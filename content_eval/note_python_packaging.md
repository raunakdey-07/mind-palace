---
title: "Note: Python Packaging and Editable Installs"
date: 2024-01-25
tags: ["python", "packaging", "note"]
document_type: "note"
status: Complete
summary: "pyproject.toml layout, console scripts, and the editable-install shebang trap"
---

# Python Packaging Notes

Distilled from packaging Mind Palace and the CLI task manager.

## Layout

- `pyproject.toml` as the single source of build config; avoid setup.py unless a build hook is unavoidable
- Console-script entry points over `if __name__ == "__main__"` for installed CLIs — but keep the main guard too, so `python -m package.module` works in source checkouts

## The Editable-Install Trap

Editable installs write absolute interpreter paths into script shebangs. Moving or renaming the virtualenv silently breaks every console script while `python -m` still works. Fix: reinstall (`pip install -e .`) after any venv relocation, and prefer `python -m` in documented commands because it survives moves.

## Versioning

Single-source the version from pyproject; never duplicate it in code constants that drift.
