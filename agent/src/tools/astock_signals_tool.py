"""Agent tool: A-share market signals (strong stocks, north flow, dragon-tiger, sector ranking, themes)."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class MarketSignalsTool(BaseTool):
    """Fetch A-share market signals: strong stocks, north-bound flow, dragon-tiger board, unlock calendar, sector rankings, theme attribution."""

    name = "get_market_signals"
    description = (
        "Fetch A-share market signals from multiple sources: "
        "1) Strong stocks with theme attribution (THS), "
        "2) North-bound capital flow minute-level, "
        "3) Dragon-tiger board rankings (individual stock or market-wide), "
        "4) Restricted-share unlock calendar, "
        "5) Sector/industry ranking by performance, "
        "6) Theme/concept attribution. "
        "Specify which signal type you need via the 'signal_type' parameter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "signal_type": {
                "type": "string",
                "enum": ["strong_stocks", "north_flow", "dragon_tiger_stock", "dragon_tiger_market",
                         "unlock_calendar", "sector_ranking", "theme_attribution", "concept_blocks"],
                "description": "Type of market signal to fetch",
            },
            "code": {"type": "string", "description": "Stock code (required for dragon_tiger_stock, unlock_calendar, concept_blocks)", "default": ""},
            "market": {"type": "string", "description": "For north_flow: 'hgt' or 'sgt'", "default": "hgt"},
            "date": {"type": "string", "description": "Date YYYY-MM-DD (for dragon_tiger_market; default today)", "default": ""},
            "days": {"type": "integer", "description": "Days ahead for unlock_calendar", "default": 90},
        },
        "required": ["signal_type"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        signal_type = str(kwargs["signal_type"])
        result: dict[str, Any] = {"signal_type": signal_type}
        try:
            if signal_type == "strong_stocks":
                result["data"] = provider.get_strong_stocks().to_dict(orient="records")
            elif signal_type == "north_flow":
                result["data"] = provider.get_north_flow(market=str(kwargs.get("market", "hgt"))).to_dict(orient="records")
            elif signal_type == "dragon_tiger_stock":
                result["data"] = provider.get_dragon_tiger_stock(str(kwargs.get("code", "")))
            elif signal_type == "dragon_tiger_market":
                result["data"] = provider.get_dragon_tiger_market(date=str(kwargs.get("date", ""))).to_dict(orient="records")
            elif signal_type == "unlock_calendar":
                result["data"] = provider.get_unlock_calendar(code=str(kwargs.get("code", "")), days=int(kwargs.get("days", 90)))
            elif signal_type == "sector_ranking":
                result["data"] = provider.get_sector_ranking().to_dict(orient="records")
            elif signal_type == "theme_attribution":
                result["data"] = provider.get_theme_attribution()
            elif signal_type == "concept_blocks":
                result["data"] = provider.get_concept_blocks(str(kwargs.get("code", "")))
        except Exception as exc:
            result["error"] = str(exc)

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
