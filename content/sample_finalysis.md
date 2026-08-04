---
title: "Finalysis: Financial Analysis Platform"
date: 2023-09-01
tags: ["python", "finance", "streamlit", "data-science"]
status: Completed
git_repo: "https://github.com/raunakdey-07/finalysis"
summary: "A Streamlit-based financial analysis tool with real-time stock data and technical indicators"
---

# Finalysis: Financial Analysis Platform

Finalysis is a Streamlit application that provides real-time financial analysis with technical indicators, portfolio tracking, and backtesting capabilities.

## Features

- **Real-time stock data** via Yahoo Finance API
- **Technical indicators**: SMA, EMA, RSI, MACD, Bollinger Bands
- **Portfolio tracker** with performance metrics (Sharpe ratio, max drawdown)
- **Backtesting engine** for strategy validation
- **Interactive charts** using Plotly

## Architecture

The backend uses Python with Pandas for data processing and TA-Lib for indicator calculations. The frontend is built with Streamlit for rapid prototyping and interactivity.

## Tech Stack

- Python 3.10, Streamlit, Pandas, TA-Lib, Plotly
- Yahoo Finance API for market data
- SQLite for portfolio storage
