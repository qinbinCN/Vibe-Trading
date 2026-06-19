# agent/src/tools/astock_news_tool.py
"""Agent tool: stock news + global financial news."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class StockNewsTool(BaseTool):
    """Fetch A-share stock news and global financial news from EastMoney."""

    name = "get_stock_news"
    description = (
        "Fetch A-share stock-specific news and/or global 7x24 financial news. "
        "Stock news covers individual stock headlines; global news covers macro "
        "and market-level headlines."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Stock code for individual stock news (optional)", "default": ""},
            "global": {"type": "boolean", "description": "Also fetch global 7x24 news", "default": False},
            "limit": {"type": "integer", "description": "Max entries per category", "default": 20},
        },
        "required": [],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        code = str(kwargs.get("code", ""))
        limit = int(kwargs.get("limit", 20))
        result: dict[str, Any] = {}
        if code:
            try:
                result["stock_news"] = provider.get_stock_news(code, limit=limit)
            except Exception as exc:
                result["stock_news_error"] = str(exc)
        if kwargs.get("global"):
            try:
                result["global_news"] = provider.get_global_news(limit=limit)
            except Exception as exc:
                result["global_news_error"] = str(exc)

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
