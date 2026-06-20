---
name: akshare
category: data-source
description: AKShare — 在线数据聚合器（18k+ stars）。回退方案，仅在 get_market_data 不可用时用于 A 股日K线。US/HK/期货/宏观/外汇仍可使用。
---

## ⛔ A股日K线：优先 get_market_data，akshare 仅作最后回退

**当 `TDX_ROOT_PATH` 已配置时，A股日K线走本地通达信 .day 文件（零网络、零 token）。**
不要写 `import akshare as ak; ak.stock_zh_a_hist(...)` — 直接用 `get_market_data` 工具。

akshare 作为回退方案，用于：
- 本地 TDX 不可用时（TDX_ROOT_PATH 未设置）
- US/HK/期货/宏观/外汇数据（本地 TDX 不覆盖）
- A股分钟线/日内数据（本地 TDX 不覆盖）

## Overview

AKShare is a free, open-source Python financial data library. Uses online HTTP scraping (Sina, East Money, etc.) — subject to throttling and IP bans.

- GitHub: https://github.com/akfamily/akshare (18k+ stars)
- Install: `pip install akshare`

## Quick Start (US/HK — NOT for A-share daily)

```python
import akshare as ak

# US stock daily
df = ak.stock_us_hist(symbol="105.AAPL", period="daily",
                       start_date="20240101", end_date="20260101", adjust="qfq")

# HK stock daily
df = ak.stock_hk_hist(symbol="00700", period="daily",
                       start_date="20240101", end_date="20260101", adjust="qfq")
```

## Top 10 High-Frequency Interfaces

### A-shares

| Function | Description | Key Params |
|----------|-------------|------------|
| `stock_zh_a_hist()` | A-share OHLCV | symbol, period, start_date, end_date, adjust |
| `stock_zh_a_spot_em()` | Real-time A-share quotes | (none) |
| `stock_individual_info_em()` | Stock basic info | symbol |
| `stock_zh_a_hist_min_em()` | Intraday bars | symbol, period(1/5/15/30/60) |

### US / HK

| Function | Description | Key Params |
|----------|-------------|------------|
| `stock_us_hist()` | US stock OHLCV | symbol (e.g. "105.AAPL"), period, start_date, end_date |
| `stock_hk_hist()` | HK stock OHLCV | symbol (e.g. "00700"), period, start_date, end_date |

### Macro / Forex / Futures

| Function | Description |
|----------|-------------|
| `macro_china_gdp()` | China GDP data |
| `macro_china_cpi()` | China CPI data |
| `futures_main_sina()` | Futures main contract quotes |
| `currency_boc_sina()` | BOC forex rates |

## Column Names

AKShare returns Chinese column names by default:

| Chinese | English | Description |
|---------|---------|-------------|
| 日期 | date | Trade date |
| 开盘 | open | Open price |
| 最高 | high | High price |
| 最低 | low | Low price |
| 收盘 | close | Close price |
| 成交量 | volume | Volume |
| 成交额 | amount | Turnover |
| 涨跌幅 | pct_change | % change |
| 换手率 | turnover_rate | Turnover rate |

## Date Format

- Input: `YYYYMMDD` string (e.g. `"20240101"`)
- Output: `日期` column as string, convert with `pd.to_datetime()`

## Symbol Format

- A-shares: pure digits `"000001"` (no .SZ suffix)
- US stocks: `"105.AAPL"` (NASDAQ prefix 105), `"106.BABA"` (NYSE prefix 106)
- HK stocks: `"00700"` (5-digit zero-padded)

## Built-in Loader

The project has a built-in AKShare DataLoader at `backtest/loaders/akshare_loader.py`. When backtesting, the runner automatically falls back to AKShare when tushare/yfinance are unavailable.

## Reference Docs

For less common interfaces, see the `references/` subdirectory or the official docs at https://akshare.akfamily.xyz/
