"""Agent tool: A-share capital flow data (margin, block trades, shareholder changes, dividend, fund flow)."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class CapitalFlowTool(BaseTool):
    """Fetch A-share capital flow data: margin trading, block trades, shareholder changes, dividend history, and fund flow."""

    name = "get_capital_flow"
    description = (
        "Fetch A-share capital-flow / position data: "
        "1) Margin trading details (融资融券), "
        "2) Block trade records (大宗交易), "
        "3) Shareholder count changes (股东户数/筹码集中度), "
        "4) Dividend/split history (分红送转), "
        "5) 120-day major/retail fund flow (主力资金流). "
        "Specify the data type via 'flow_type'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "flow_type": {
                "type": "string",
                "enum": ["margin", "block_trades", "shareholder", "dividend", "fund_flow_120d"],
                "description": "Type of capital flow data to fetch",
            },
            "code": {"type": "string", "description": "Stock code", "default": ""},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (optional)", "default": ""},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)", "default": ""},
        },
        "required": ["flow_type", "code"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        flow_type = str(kwargs["flow_type"])
        code = str(kwargs.get("code", ""))
        start = str(kwargs.get("start_date", ""))
        end = str(kwargs.get("end_date", ""))
        result: dict[str, Any] = {"flow_type": flow_type, "code": code}
        try:
            if flow_type == "margin":
                result["data"] = provider.get_margin_trading(code, start_date=start, end_date=end)
            elif flow_type == "block_trades":
                result["data"] = provider.get_block_trades(code, start_date=start, end_date=end)
            elif flow_type == "shareholder":
                result["data"] = provider.get_shareholder_changes(code)
            elif flow_type == "dividend":
                result["data"] = provider.get_dividend_history(code)
            elif flow_type == "fund_flow_120d":
                result["data"] = provider.get_fund_flow_120d(code).to_dict(orient="records")
        except Exception as exc:
            result["error"] = str(exc)

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
