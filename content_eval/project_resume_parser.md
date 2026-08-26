---
title: "Project: Resume Parsing and Skill Extraction"
date: 2024-04-02
tags: ["python", "nlp", "parsing"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/resume-parser"
summary: "Structured extraction of skills, roles, and education from resume documents"
---

# Resume Parser

NER-and-structure extraction over resumes — the parsing cousin of the PDF extraction project.

## Pipeline

- Section segmentation (experience, education, skills) via heading heuristics
- **Skill extraction** against a curated taxonomy with fuzzy matching for variants
- Entity dates normalization (Jan 2020 – Present → ISO ranges)
- Output to structured JSON matching a fixed schema

## Evaluation

Hand-labeled 100-resume sample; skill extraction F1 0.91, date normalization accuracy 0.96. Section mis-detection was the dominant error source — same failure class as the PDF pipeline.
