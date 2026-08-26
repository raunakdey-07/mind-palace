---
title: "Project: Subscription and Recurring Payment Tracker"
date: 2024-04-24
tags: ["python", "finance", "streamlit"]
document_type: "project"
status: Completed
git_repo: "https://github.com/raunakdey-07/subscriptions"
summary: "Detecting recurring charges from transaction history with renewal forecasting"
---

# Subscription Tracker

Mining transaction history for recurring charges — pattern detection over the finance corpus.

## Detection

- Merchant + amount clustering across months
- Interval regularity scoring (weekly/monthly/annual cadences)
- Price-increase detection when amounts drift within tolerance
- Cancellation candidate ranking by usage-vs-cost

## Output

Monthly cost forecast, newly-detected subscriptions, and lapsed ones. The recurring-payment model fed directly into the finance dashboard's forward-looking views.
