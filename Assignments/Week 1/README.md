# Portfolio Rebalancing Engine

## Overview

This project is a multi-account portfolio rebalancing engine built in Python.

The program retrieves live investment holdings through Plaid and analyzes investment accounts across multiple financial institutions. It compares each account's current allocation against a target portfolio model and calculates the trades required to rebalance the account.

## Features

- Retrieves live holdings using Plaid
- Supports multiple brokerage and investment accounts
- Calculates current portfolio weights
- Compares current weights with target allocations
- Measures portfolio drift
- Calculates target dollar values
- Generates BUY, SELL, and HOLD recommendations
- Calculates fractional-share trades
- Simulates whole-share rebalancing
- Calculates post-trade portfolio weights
- Flags tracking errors greater than 0.50 percentage points
- Generates a formatted Excel report
- Creates an account-level dashboard
- Emails the completed Excel report automatically
- Stores the Gmail App Password securely in macOS Keychain

## Files

### assignment_1b.py
Main portfolio analysis, rebalancing, Excel-reporting, and email-delivery engine.

### plaid_data.py
Retrieves and organizes investment account and holdings data from Plaid.

### assignment_1b_questions.md
Written responses covering implementation constraints and portfolio-management considerations.

## Technologies

- Python
- Plaid CLI
- openpyxl
- Gmail SMTP
- macOS Keychain

## How It Works

1. Plaid supplies holdings from linked investment accounts.
2. The program organizes holdings by account and ticker.
3. Current weights are compared with the target model.
4. Fractional and whole-share rebalance plans are calculated.
5. Post-trade tracking error is measured.
6. A formatted Excel workbook is created.
7. The workbook can be emailed automatically.

## Security

No brokerage credentials or Gmail App Passwords are stored in this repository. Email credentials are read from environment variables or macOS Keychain.

## Purpose

This project was created as part of quantitative-trading preparation coursework to practice Python, portfolio mathematics, data structures, automation, and financial analysis.
