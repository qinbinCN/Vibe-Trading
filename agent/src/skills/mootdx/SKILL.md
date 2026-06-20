---
name: mootdx
category: data-source
description: Mootdx A-share market data via TCP-direct 通达信 servers. 仅在需要分钟线或 get_market_data 不可用时使用。日K线和财务数据优先使用 get_market_data + get_financial_snapshot（本地离线）。
---

## ⛔ USE TOOLS FIRST — NOT RAW SCRIPTS

**For daily OHLCV (日K线): call `get_market_data` tool.**  When `TDX_ROOT_PATH` is set
in .env, it reads local .day files directly from your TDX installation — zero network,
no TCP servers, no API keys, includes turnover_rate (换手率).  Only fall back to raw
mootdx scripts when you need **intraday bars (1m/5m/15m/30m/1H)** or when
`get_market_data` is unavailable.

**For financial data: call `get_financial_snapshot` tool.**  It reads local gpcw*.zip
files (576 Chinese-named fields) when `TDX_ROOT_PATH` is set.

**Do NOT write `from mootdx.quotes import Quotes` for daily K-line.**  Use the tools
above — they handle routing, caching, fallback, and data normalization automatically.

## Overview

Mootdx talks the native 通达信 (TDX) binary protocol over TCP.  It is useful for
**intraday bars** (1m/5m/15m/30m/1H) and real-time quotes — data the local tdx_local
loader does not cover.

- GitHub: https://github.com/mootdx/mootdx
- Install: `pip install mootdx && pip install 'httpx>=0.28.1'`

## Intraday Quick Start (use only when get_market_data cannot serve)

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market="std")

# Intraday bars — offset-from-latest only, no native date range.
df_15m = client.bars(symbol="600519", frequency=1, offset=800)
```

## Built-in Loader

`backtest/loaders/mootdx_loader.py` is registered as the `mootdx` source.
The A-share fallback chain is now `["tdx_local", "tencent", "mootdx", "eastmoney",
"baostock", "akshare", "tushare", "local"]` — tdx_local (本地离线) wins when
`TDX_ROOT_PATH` is configured; mootdx is a network fallback.

## Known Limitations

| Limitation | Workaround |
|------------|------------|
| Requires network connectivity to TDX TCP servers | Use tdx_local (local .day files) for daily |
| Each `bars()` page is 800 rows | Loader paginates back up to 25 pages |
| Returns 前复权 by default | Use tushare/akshare for raw prices |
| No turnover rate (换手率) in .day format | tdx_local loader computes it locally from gpcw |
