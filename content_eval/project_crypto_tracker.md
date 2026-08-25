---
title: "Project: Crypto Portfolio Tracker"
date: 2024-02-18
tags: ["python", "finance", "streamlit", "crypto"]
document_type: "project"
status: In Progress
git_repo: "https://github.com/raunakdey-07/crypto-tracker"
summary: "Real-time cryptocurrency portfolio tracker with exchange API integration"
---

# Crypto Portfolio Tracker

Live cryptocurrency holdings dashboard — similar surface area to Finalysis but for digital assets.

## Features

- **Exchange API** integration (Binance, Coinbase) via ccxt
- Real-time valuation with websocket price streams
- Transaction ledger import from CSV exports
- Cost-basis tracking across wallets (FIFO)
- Tax-season export of realized gains

## Stack

Python, Streamlit, ccxt, websockets, PostgreSQL for the transaction store.
