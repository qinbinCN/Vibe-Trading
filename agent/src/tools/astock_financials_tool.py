# agent/src/tools/astock_financials_tool.py
"""Agent tool: A-share financial statements (balance sheet, income, cashflow)."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class StockFinancialsTool(BaseTool):
    """Fetch A-share financial statements: balance sheet, income statement, and cashflow."""

    name = "get_stock_financials"
    description = (
        "Fetch A-share stock financial statements from Sina Finance. "
        "Supports balance sheet (资产负债表), income statement (利润表), "
        "and cashflow statement (现金流量表). Returns each as a table "
        "with reporting periods as rows and line items as columns."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Stock code, e.g. 600519"},
            "report_type": {
                "type": "string",
                "enum": ["balance_sheet", "income_statement", "cashflow"],
                "description": "Financial statement type",
                "default": "balance_sheet",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        code = str(kwargs["code"])
        report_type = str(kwargs.get("report_type", "balance_sheet"))
        try:
            df = provider.get_financial_statements(code, report_type=report_type)
            result = {
                "code": code,
                "report_type": report_type,
                "data": df.to_dict(orient="records") if not df.empty else [],
                "row_count": len(df),
            }
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
