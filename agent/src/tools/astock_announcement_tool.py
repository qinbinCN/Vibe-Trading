# agent/src/tools/astock_announcement_tool.py
"""Agent tool: A-share announcements via CNInfo (巨潮)."""
from __future__ import annotations

import json
from typing import Any

from backtest.data_providers.registry import get_provider
from src.agent.tools import BaseTool


class AnnouncementTool(BaseTool):
    """Search A-share stock announcements (full-text) via CNInfo (巨潮资讯网)."""

    name = "get_announcements"
    description = (
        "Search A-share stock announcements via CNInfo (巨潮资讯网), covering "
        "Shanghai/Shenzhen/Beijing exchanges. Supports keyword full-text search "
        "and date range filtering. Returns announcement title, URL, publish date, "
        "stock code, and stock name."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Stock code filter, e.g. 600519 (optional)", "default": ""},
            "keyword": {"type": "string", "description": "Full-text keyword search (optional)", "default": ""},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (optional)", "default": ""},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)", "default": ""},
            "page": {"type": "integer", "description": "Page number", "default": 1},
            "page_size": {"type": "integer", "description": "Results per page", "default": 30},
        },
        "required": [],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        provider = get_provider("astock")
        if provider is None:
            return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)

        try:
            announcements = provider.get_announcements(
                code=str(kwargs.get("code", "")),
                keyword=str(kwargs.get("keyword", "")),
                start_date=str(kwargs.get("start_date", "")),
                end_date=str(kwargs.get("end_date", "")),
                page=int(kwargs.get("page", 1)),
                page_size=int(kwargs.get("page_size", 30)),
            )
            return json.dumps({"announcements": announcements, "count": len(announcements)}, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
