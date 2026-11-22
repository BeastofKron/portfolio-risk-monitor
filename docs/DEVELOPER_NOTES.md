# Portfolio Monitor

A 4-layer options + equity portfolio monitor.  
Reports conditions, tracks your targets, reads the news. **Never recommends trades.**

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."   # only needed for news analysis
```

## Edit your positions

Open `positions.json` and add your options and shares.

## Run

```bash
python main.py                         # today
python main.py --asof 2025-12-19       # specific date
python main.py --skip-news             # skip Claude API
```

## Dashboard

```bash
streamlit run layer4/dashboard.py -- --asof 2025-12-19
```

## Cron (weekdays, 5 PM ET)

```
0 17 * * 1-5 cd /path/to/portfolio_monitor && python main.py >> logs/monitor.log 2>&1
```

## Config

All thresholds are in `config.json`:
- `risk_free_rate` — Black-Scholes r (default 0.045)
- `sector_map` — ticker → sector overrides (yfinance used as fallback)
- `concentration.ticker_cap_pct` — warn if ticker > N% of book (default 40)
- `concentration.sector_cap_pct` — warn if sector > N% of book (default 60)
- `iv_rank.lookback_days` — IV rank lookback (default 252)
- `dte_warning_days` — flag positions expiring within N days (default 45)
- `macro_gate.weights` — blend weights for VIX/breadth/credit (sum to 1.0)
- `news.window_days` — headlines window for Claude (default 3)
- `alerts.strike_band_pct` — new-strike detection band (default ±30%)
- `notifier.enabled` — set true to push via Telegram or iMessage

## Architecture

```
Layer 1  data_pull.py   → yfinance spot + full chain
         greeks.py      → Black-Scholes (no external lib)
         valuation.py   → mark, value, P&L, DTE, progress
         storage.py     → SQLite + JSON snapshots

Layer 2  analytics.py   → allocation, aggregate Greeks, IV rank, DTE flags

Layer 3  macro_gate.py  → deterministic 0-100 macro score (VIX/breadth/credit)
         news.py        → Claude API news analysis, cached per day

Layer 4  alerts.py      → chain diff + condition alerts
         dashboard.py   → Streamlit Bloomberg-style dashboard
         notifier.py    → Telegram / iMessage push (optional)
```
