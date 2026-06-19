"""Agent tool: stock fundamental profile (financial snapshot, F10, basic info)."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class StockProfileTool(BaseTool):
    """Fetch A-share stock fundamental profile: financial snapshot, F10 data, and basic info."""

    name = "get_stock_profile"
    description = (
        "Fetch A-share stock fundamental profile from multiple sources: "
        "quarterly financial snapshot (37 fields via mootdx), F10 company data, "
        "and basic stock info (industry, shares, market cap, listing date via EastMoney). "
        "Use this before analyzing a stock's fundamentals."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Stock code, e.g. 600519, 000001, 688017"},
            "f10_category": {
                "type": "string",
                "description": "F10 category: gszl(公司资料), gdbd(股东变动), cwbl(财务比率), gsgg(公司公告)",
                "default": "gszl",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        code = str(kwargs["code"])
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        result: dict[str, Any] = {"code": code}
        try:
            result["financial_snapshot"] = provider.get_financial_snapshot(code)
        except Exception as exc:
            result["financial_snapshot_error"] = str(exc)
        try:
            result["stock_info"] = provider.get_stock_info(code)
        except Exception as exc:
            result["stock_info_error"] = str(exc)
        try:
            cat = kwargs.get("f10_category", "gszl")
            result["f10"] = provider.get_company_f10(code, category=str(cat))
        except Exception as exc:
            result["f10_error"] = str(exc)

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
