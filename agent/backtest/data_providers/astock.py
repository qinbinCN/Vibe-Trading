# agent/backtest/data_providers/astock.py
"""AStockDataProvider — wraps a-stock-data 27 endpoints for A-share market data.

Data sources (priority: mootdx/Tencent > Sina/CNInfo/THS > EastMoney):
  Layer 1 - 行情:  mootdx (TCP), Tencent (HTTP), Baidu (HTTP)
  Layer 2 - 研报:  EastMoney reportapi, THS consensus, iwencai NL search
  Layer 3 - 信号:  THS hotspots/north-flow, EastMoney slist/push2/datacenter
  Layer 4 - 资金面: EastMoney datacenter (margin/block/shareholder/dividend/fund_flow)
  Layer 5 - 新闻:  EastMoney search + np-weblist
  Layer 6 - 基础数据: mootdx finance/F10, EastMoney push2, Sina finance
  Layer 7 - 公告:  CNInfo (cninfo.com.cn)

Rate-limiting: All eastmoney.com requests go through em_get() with serial
throttling (>=1s interval + jitter) and session reuse. See upstream SKILL.md.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.request
from typing import Any

import pandas as pd
import requests

from backtest.data_providers.registry import register_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared infrastructure (from a-stock-data SKILL.md)
# ---------------------------------------------------------------------------

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


def em_get(url: str, params: dict | None = None, headers: dict | None = None,
           timeout: int = 15, **kwargs: Any) -> requests.Response:
    """EastMoney unified request: auto throttle + session reuse + default UA."""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(report_name: str, columns: str = "ALL",
                          filter_str: str = "", page_size: int = 50,
                          sort_columns: str = "", sort_types: str = "-1") -> list[dict]:
    """EastMoney datacenter unified query (dragon-tiger, unlock, margin, block, shareholder, dividend)."""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# Ticker normalisation
def _get_prefix(code: str) -> str:
    code = str(code).replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    code = code.replace("SH", "").replace("SZ", "").replace("BJ", "").strip().zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    elif code.startswith("8"):
        return "bj" + code
    else:
        return "sz" + code


def _strip_code(code: str) -> str:
    """Normalize any ticker format to pure 6-digit string."""
    code = str(code).upper().strip()
    code = re.sub(r"^(SH|SZ|BJ)", "", code)
    code = re.sub(r"\.(SH|SZ|BJ)$", "", code)
    return code.zfill(6)


# Module-level helpers
def _safe_float(v: str) -> float | None:
    try:
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def _safe_int(v: str) -> int | None:
    try:
        return int(v) if v else None
    except (ValueError, TypeError):
        return None
