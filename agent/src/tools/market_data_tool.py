"""Local market data tool backed by the shared loader layer."""

from __future__ import annotations

from typing import Any

from src.agent.tools import BaseTool
from src.market_data import DEFAULT_MAX_ROWS, fetch_market_data_json


class MarketDataTool(BaseTool):
    """Fetch normalized OHLCV data through repository loaders."""

    name = "get_market_data"
    description = (
        "Fetch normalized OHLCV market data through the repository loader layer. "
        "For A-shares, uses local TDX .day files when TDX_ROOT_PATH is set (zero "
        "network).  To avoid output truncation when you only need current price "
        "and recent trend, use fields=['close'] and max_rows=10.  For full OHLCV "
        "analysis, omit fields to get all columns (open,high,low,close,volume,turnover_rate)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Symbols such as ["600519.SH"], ["AAPL.US"], ["700.HK"].',
            },
            "start_date": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format.",
            },
            "end_date": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format.",
            },
            "source": {
                "type": "string",
                "enum": [
                    "auto",
                    "yfinance",
                    "yahoo",
                    "okx",
                    "ccxt",
                    "tushare",
                    "baostock",
                    "tencent",
                    "akshare",
                    "mootdx",
                    "eastmoney",
                    "sina",
                    "stooq",
                    "finnhub",
                    "alphavantage",
                    "tiingo",
                    "fmp",
                ],
                "description": (
                    "Data source. 'auto' detects from symbol format with fallback. "
                    "Free, no key: yfinance/yahoo (US/HK equities), okx/ccxt "
                    "(crypto), baostock/tencent/eastmoney/sina/akshare/mootdx "
                    "(China A-shares), stooq (global EOD). Key-gated REST: tushare "
                    "(China A-shares), finnhub/alphavantage/tiingo/fmp (US/global)."
                ),
                "default": "auto",
            },
            "interval": {
                "type": "string",
                "description": "Bar size, e.g. 1D, 1H, 4H, 30m.",
                "default": "1D",
            },
            "max_rows": {
                "type": "integer",
                "description": "Per-symbol row cap (default 60). Set lower (5–10) when you only need current price. Use 0 for full series.",
                "default": 60,
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Columns to return. When you ONLY need price: ['close']. For PE/PB: ['close','turnover_rate']. Omit for full OHLCV (open,high,low,close,volume,turnover_rate).",
            },
        },
        "required": ["codes", "start_date", "end_date"],
    }

    def execute(self, **kwargs: Any) -> str:
        result = fetch_market_data_json(
            codes=kwargs["codes"],
            start_date=kwargs["start_date"],
            end_date=kwargs["end_date"],
            source=kwargs.get("source", "auto"),
            interval=kwargs.get("interval", "1D"),
            max_rows=kwargs.get("max_rows", 60),
        )
        # Post-filter fields if requested
        fields = kwargs.get("fields")
        if fields and isinstance(fields, list) and len(fields) > 0:
            import json
            data = json.loads(result)
            allowed = set(fields)
            for sym in list(data.keys()):
                if sym.startswith("_"):
                    continue
                rows = data[sym]
                if isinstance(rows, dict):
                    # cap_rows returned truncated envelope
                    inner = rows.get("data", [])
                    data[sym]["data"] = [
                        {k: v for k, v in row.items() if k in allowed}
                        for row in inner
                    ]
                elif isinstance(rows, list):
                    data[sym] = [
                        {k: v for k, v in row.items() if k in allowed}
                        for row in rows
                    ]
            return json.dumps(data, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        return result
