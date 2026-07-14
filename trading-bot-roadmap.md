# Trading Bot — Operational Roadmap & Context

> **For Claude Code:** This document is context, not a build spec. This file explains the project's lifecycle stages, success gates, and target universe so implementation decisions align with how the system will actually be used. Keep this file in the repo root; treat it as the source of truth for symbols and gate criteria.

---

## Project lifecycle — four stages

The bot moves through stages. Each stage has a **gate**: explicit pass criteria to advance, and kill criteria. Code should support measuring these gates, not just trading.

### Stage 1 — Historical validation (current)
- Run all strategies through the backtest engine on maximum available Upstox 5-min history
- **Pass gate (per strategy):** positive out-of-sample expectancy after full costs, OOS expectancy ≥ 50% of in-sample, max drawdown < 15% of capital, ≥ 60 trades in the full period
- Strategies that fail are disabled in `ACTIVE_STRATEGIES` in config, not deleted
- If all strategies fail: iterate on filters/params (sparingly — every tuned parameter increases overfit risk), re-run. Do not proceed to Stage 2 on hope.

### Stage 2 — Paper trading (target: 2–3 months, minimum ~100 trades)
- Only Stage-1 survivors run, via `ACTIVE_STRATEGIES`
- **The key measurement: paper expectancy vs backtest expectancy for the same period.** The EOD/weekly report must show this comparison per strategy. Large divergence (paper < 50% of backtest expectancy) = investigate fill model, look-ahead bugs, or regime change before anything else.
- No parameter changes mid-stage. If a change is needed, the clock restarts.
- Run weekly: `python -m storage.weekly_report`

### Stage 3 — Gate to live
- **Pass criteria:** ≥ 100 paper trades, positive expectancy after costs, paper ≈ backtest (within tolerance), drawdown acceptable, zero unexplained system incidents (missed exits, state corruption) in final month
- Go-live starts at **25% of configured capital** for the first month, scaling up only if live expectancy holds
- `PAPER_TRADING = False` is the only switch; nothing else changes

### Stage 4 — Ongoing operation
- Weekly: trade journal review (per-strategy, per-symbol expectancy) via `python -m storage.weekly_report`
- Monthly: re-run backtest with newly accumulated data appended; a strategy whose rolling 3-month expectancy goes negative gets disabled
- Edges decay. The system's job includes telling us when to stop.

**Implication for code:** the reporting layer (`backtest/report.py` + `storage/weekly_report.py` + EOD summaries) should make gate metrics first-class outputs — expectancy, trade count, drawdown, and backtest-vs-paper comparison — not require manual computation.

---

## Symbol universe

Selection criteria: Nifty-50-grade liquidity (tight spreads → slippage model stays valid), sufficient intraday volatility to clear costs, no small/microcaps.

| Symbol | NSE code | Upstox instrument key | Role |
|--------|----------|-----------------------|------|
| HDFC Bank | HDFCBANK | `NSE_EQ\|INE040A01034` | Core liquidity; ORB + trend |
| ICICI Bank | ICICIBANK | `NSE_EQ\|INE090A01021` | Core liquidity; ORB + trend |
| SBI | SBIN | `NSE_EQ\|INE062A01020` | Core liquidity; higher beta |
| Infosys | INFY | `NSE_EQ\|INE009A01021` | Gap/ORB candidate |
| TCS | TCS | `NSE_EQ\|INE467B01029` | Gap/ORB candidate; low beta |
| Reliance | RELIANCE | `NSE_EQ\|INE002A01018` | Highest-volume; all strategies |
| Tata Motors | TATAMOTORS | `NSE_EQ\|INE155A01022` | High beta; mean-reversion |
| Bajaj Finance | BAJFINANCE | `NSE_EQ\|INE296A01024` | High beta; mean-reversion |
| Adani Enterprises | ADANIENT | `NSE_EQ\|INE423A01024` | Highest beta; mean-reversion |

> **Note on ISINs:** Verify against the Upstox master contract file before live trading. ISINs can change on corporate actions. Do not trade a symbol whose ISIN you haven't cross-checked.

- Universe is deliberately small (~9) to limit feed load and multiple-testing bias. Expansion is a `config.py` change, not a code change.

## Strategy × symbol mapping

Strategies specialise by nature; the mapping in `config.py` (`ACTIVE_STRATEGIES`) is data-driven by backtest results:

| Strategy | Intended symbols | Rationale |
|----------|-----------------|-----------|
| `ema_vwap_rsi` | All | Trend-following; works on any liquid name |
| `orb` | INFY, TCS, RELIANCE, HDFCBANK, ICICIBANK | Index-sensitive / gap-prone names |
| `mean_reversion` | TATAMOTORS, BAJFINANCE, ADANIENT | High-beta mean-reversion candidates only; never index |

Per-(strategy, symbol) expectancy from backtest drives refinement of this table.

---

## Gate criteria (also in config.py)

| Metric | Stage-1 threshold | Stage-2 threshold |
|--------|-------------------|-------------------|
| OOS expectancy | > 0 | > 0 (rolling) |
| OOS / IS ratio | ≥ 50% | paper / backtest ≥ 50% |
| Max drawdown | < 15% of capital | < 15% of capital |
| Trade count | ≥ 60 full period | ≥ 20 per week |

These values are the `GATE_*` constants in `config.py`. Change them there; the reporting code reads from config.

---

## CLI reference

```bash
# Stage-1: backtest with gate verdicts
python -m backtest --strategies all --from 2024-01-01 --to 2026-06-30

# Download historical data first
python -m backtest.downloader --from 2024-01-01 --to 2026-06-30

# Stage-2: weekly paper-vs-backtest comparison
python -m storage.weekly_report --from 2025-06-01 --to 2025-06-30

# Run bot in paper mode
python main.py

# Dashboard
streamlit run dashboard/app.py
```

---

## Principles (do not violate)

1. **No stage-skipping.** Advancement requires passing the gate, never optimism.
2. **Paper trades the real intended capital** (₹1,00,000 notional) so results are behaviorally meaningful.
3. **Conservative assumptions everywhere:** SL-first on ambiguous candles, fail-closed news filter, slippage against us.
4. **Kill criteria are features.** A backtest that kills a strategy, or a monthly review that disables one, is the system working — build the reporting to make these calls easy and obvious.
5. **"Foolproof" is not the goal.** Validated small edge + controlled downside + knowing when to stop is the goal.
