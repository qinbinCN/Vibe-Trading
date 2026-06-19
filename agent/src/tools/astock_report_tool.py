# agent/src/tools/astock_report_tool.py
"""Agent tool: search research reports + consensus EPS."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class ResearchReportTool(BaseTool):
    """Search A-share research reports with optional keyword and stock code filtering."""

    name = "get_research_reports"
    description = (
        "Search A-share research reports from EastMoney report database. "
        "Returns report title, organization, rating, date, PDF URL, and 3-year EPS estimates. "
        "Can also fetch consensus EPS estimates from THS (Tonghuashun)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Stock code filter, e.g. 600519 (optional)", "default": ""},
            "keyword": {"type": "string", "description": "Keyword search for reports (optional)", "default": ""},
            "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
            "page_size": {"type": "integer", "description": "Results per page", "default": 20},
            "consensus_eps": {"type": "boolean", "description": "Also fetch consensus EPS estimates", "default": False},
        },
        "required": [],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        code = str(kwargs.get("code", ""))
        keyword = str(kwargs.get("keyword", ""))
        page = int(kwargs.get("page", 1))
        page_size = int(kwargs.get("page_size", 20))

        result: dict[str, Any] = {}
        try:
            result["reports"] = provider.get_research_reports(code=code, keyword=keyword, page=page, page_size=page_size)
        except Exception as exc:
            result["reports_error"] = str(exc)
        if kwargs.get("consensus_eps") and code:
            try:
                result["consensus_eps"] = provider.get_consensus_eps(code)
            except Exception as exc:
                result["consensus_eps_error"] = str(exc)

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
