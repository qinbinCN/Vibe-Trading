# agent/src/tools/financial_snapshot_tool.py
"""Agent tool: financial snapshot from local TDX gpcw (优先本地) or mootdx TCP."""

from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class FinancialSnapshotTool(BaseTool):
    """Fetch the latest financial snapshot for an A-share stock.

    Reads from local TongDaXin gpcw data when TDX_ROOT_PATH is configured
    (584 fields with Chinese names, zero network calls).  Falls back to
    mootdx TCP when local data is unavailable.
    """

    name = "get_financial_snapshot"
    description = (
        "Fetch the latest quarterly financial snapshot for an A-share stock. "
        "Returns ~580 financial metrics with Chinese field names including: "
        "基本每股收益 (EPS), 每股净资产 (NAV per share), 净资产收益率 (ROE), "
        "营业收入 (revenue), 归属于母公司所有者的净利润 (net profit), "
        "资产总计 (total assets), 负债合计 (total liabilities), "
        "总股本 (total shares), 自由流通股 (free float), and many more. "
        "When TDX_ROOT_PATH is configured, reads directly from local gpcw "
        "files — NO network calls, NO API keys, NO rate limits."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Stock code (6 digits), e.g. 600519 for Kweichow Moutai",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: specific field names to return. If omitted, returns all ~580 fields.",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps(
                {"status": "error", "error": "AStockDataProvider unavailable"},
                ensure_ascii=False,
            )

        code = str(kwargs["code"])
        requested_fields = kwargs.get("fields")

        try:
            snapshot = provider.get_financial_snapshot(code)

            if not snapshot:
                return json.dumps(
                    {"status": "error", "error": f"No financial data found for {code}"},
                    ensure_ascii=False,
                )

            source = snapshot.pop("_source", "unknown")
            report_date = snapshot.pop("_report_date", "unknown")

            # Filter to requested fields if specified
            if requested_fields:
                filtered = {}
                for key in requested_fields:
                    if key in snapshot:
                        filtered[key] = snapshot[key]
                snapshot = filtered

            result = {
                "code": code,
                "source": source,          # "tdx_local" or "mootdx_tcp"
                "report_date": report_date,
                "field_count": len(snapshot),
                "data": snapshot,
            }
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            )
