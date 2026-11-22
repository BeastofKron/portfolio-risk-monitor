# Portfolio Risk Monitor

> A four-layer options and equity monitoring pipeline: market data, Black-Scholes Greeks, risk analytics, a macro regime gate, and Claude-summarized news, surfaced in a Streamlit dashboard. **It reports conditions; it never recommends trades.**

![Python](https://img.shields.io/badge/Python-3.11-3776AB) ![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-FF4B4B) ![SQLite](https://img.shields.io/badge/SQLite-storage-003B57) ![Claude](https://img.shields.io/badge/Anthropic-Claude-D97757)

## Architecture

```
Layer 1  Data & valuation   yfinance spot + chains → Black-Scholes Greeks (no external lib) → mark, P&L, DTE → SQLite + JSON snapshots
Layer 2  Analytics          allocation, aggregate Greeks, IV rank, concentration caps, DTE warnings
Layer 3  Context            macro gate (VIX level/term, breadth, credit spreads) + Claude news digest
Layer 4  Output             threshold alerts, optional notifier, Streamlit dashboard
```

All thresholds live in `config.json` (risk-free rate, concentration caps, IV-rank windows, macro weights).

## Run

```bash
pip install -r requirements.txt
cp positions.example.json positions.json   # positions.json is git-ignored
python main.py --skip-news
streamlit run layer4/dashboard.py
```

## Engineering highlights

- Greeks implemented from first principles
- Layered design: each layer is independently runnable and testable
- LLM used for **summarization only**, with explicit no-advice guardrails
- Config-driven thresholds and a cron-ready daily runner

> Disclaimer: educational and engineering demonstration only. Not financial advice.

## Roadmap

- Unit tests validating Greeks against reference values
- Historical replay and backfill mode
- Docker image for scheduled runs

## Topics
`python` `options` `black-scholes` `risk-analytics` `streamlit` `data-pipeline` `quant` `claude`
