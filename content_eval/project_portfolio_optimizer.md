---
title: "Project: Portfolio Optimizer with Mean-Variance Analysis"
date: 2023-10-12
tags: ["python", "finance", "optimization", "portfolio"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/portfolio-optimizer"
summary: "Markowitz mean-variance portfolio optimization with efficient frontier visualization"
---

# Portfolio Optimizer

Mean-variance portfolio construction — distinct from Finalysis's tracking features; this one *allocates* rather than monitors.

## Features

- **Efficient frontier** computation via quadratic programming (cvxpy)
- **Sharpe-optimal** and minimum-variance portfolios
- Constraint support: sector caps, min weights, turnover limits
- Monte Carlo simulation of random portfolios for comparison
- Streamlit dashboard for interactive frontier exploration

## Math

Covariance estimated via Ledoit-Wolf shrinkage; expected returns from historical means with Black-Litterman optional overlay.
