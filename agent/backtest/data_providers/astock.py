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


@register_provider
class AStockDataProvider:
    """A-stock full-stack data provider — 27 endpoints, 7 layers."""

    name = "astock"
    version = "3.2.2"

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        try:
            import mootdx
            return True
        except ImportError:
            return False

    def check_prerequisites(self) -> list[str]:
        missing = []
        try:
            import mootdx
        except ImportError:
            missing.append("mootdx>=0.10")
        try:
            import stockstats
        except ImportError:
            missing.append("stockstats")
        return missing

    # ------------------------------------------------------------------
    # Layer 1: 行情 (3 endpoints) — mootdx + Tencent, no IP block risk
    # ------------------------------------------------------------------

    def get_kline(self, code: str, category: int = 4, offset: int = 100) -> pd.DataFrame:
        """Fetch K-line data via mootdx TCP (no auth, no IP block).

        Args:
            code: Stock code, e.g. '688017', '000001'
            category: 4=日线, 5=周线, 6=月线, 7=1m, 8=5m, 9=15m, 10=30m, 11=60m
            offset: Number of bars to fetch
        Returns:
            DataFrame with columns: open, close, high, low, vol, amount, datetime
        """
        from mootdx.quotes import Quotes
        market = 1 if _strip_code(code).startswith(("6", "9")) else 0
        client = Quotes.factory(market="std")
        df = client.bars(symbol=_strip_code(code), category=category, offset=offset)
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
        return df if df is not None else pd.DataFrame()

    def get_realtime_quote(self, codes: list[str]) -> dict[str, dict]:
        """Fetch real-time quotes via Tencent Finance HTTP (no IP block).

        Returns dict keyed by normalized code with fields:
          price, open, high, low, last_close, pe, pb, market_cap,
          turnover_rate, limit_up, limit_down, volume, amount, change_pct
        """
        prefix_map = {c: _get_prefix(c) for c in codes}
        tencent_codes = [prefix_map[c] for c in codes]
        url = f"https://qt.gtimg.cn/q={','.join(tencent_codes)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        results: dict[str, dict] = {}
        for c in codes:
            prefix = prefix_map[c]
            # each line: v_sh600519="1~茅台~600519~..."
            pattern = re.compile(rf'v_{re.escape(prefix)}="(.*?)"')
            m = pattern.search(raw)
            if not m:
                continue
            fields = m.group(1).split("~")
            if len(fields) < 50:
                continue
            results[_strip_code(c)] = {
                "code": _strip_code(c),
                "name": fields[1],
                "price": _safe_float(fields[3]),
                "last_close": _safe_float(fields[4]),
                "open": _safe_float(fields[5]),
                "volume": _safe_int(fields[6]),
                "high": _safe_float(fields[33]),
                "low": _safe_float(fields[34]),
                "amount": _safe_float(fields[37]),
                "change_pct": _safe_float(fields[32]),
                "pe": _safe_float(fields[39]),
                "pb": _safe_float(fields[46]),
                "market_cap": _safe_float(fields[45]),
                "turnover_rate": _safe_float(fields[38]),
                "limit_up": _safe_float(fields[47]),
                "limit_down": _safe_float(fields[48]),
            }
        return results

    def get_index_quote(self, codes: list[str]) -> dict[str, dict]:
        """Fetch index/ETF quotes via Tencent. Accepts codes like ['sh000001', 'sz399006']."""
        return self.get_realtime_quote(codes)

    # ------------------------------------------------------------------
    # Layer 2: 研报 (4 endpoints) — eastmoney reportapi + THS + iwencai
    # ------------------------------------------------------------------

    def get_research_reports(self, code: str = "", keyword: str = "",
                              page: int = 1, page_size: int = 20) -> list[dict]:
        """Fetch research reports via EastMoney reportapi.

        Args:
            code: Optional stock code filter
            keyword: Optional keyword search
            page: Page number (1-based)
            page_size: Results per page
        Returns:
            List of dicts with keys: title, org_name, rating, date, pdf_url, eps_2025, eps_2026, eps_2027
        """
        params: dict[str, str] = {
            "pageNumber": str(page), "pageSize": str(page_size),
            "sortColumns": "NOTICE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        }
        if code:
            params["filter"] = f'(INDUSTRY_CODE="{_strip_code(code)}")'
        if keyword:
            params["keyword"] = keyword
        r = em_get("https://reportapi.eastmoney.com/report/list", params=params, timeout=15)
        d = r.json()
        if d.get("result") and d["result"].get("data"):
            return d["result"]["data"]
        return []

    def download_report_pdf(self, url: str, save_path: str = "") -> bytes:
        """Download a research report PDF from EastMoney URL."""
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=30)
        r.raise_for_status()
        if save_path:
            with open(save_path, "wb") as f:
                f.write(r.content)
        return r.content

    def get_consensus_eps(self, code: str) -> dict[str, dict]:
        """Fetch consensus EPS estimates via THS (basic.10jqka.com.cn).

        Returns dict keyed by year with fields: eps, high, low, analyst_count
        """
        clean = _strip_code(code)
        url = f"https://basic.10jqka.com.cn/{clean}/worth.html"
        headers = {"User-Agent": UA}
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "gbk"
        html = r.text
        results: dict[str, dict] = {}
        pattern = r'"ycpj":(\[.*?\])'
        m = re.search(pattern, html, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            for item in data:
                year = str(item.get("year", ""))
                results[year] = {
                    "eps": item.get("eps"),
                    "high": item.get("high"),
                    "low": item.get("low"),
                    "analyst_count": item.get("analystCount"),
                }
        return results

    def search_reports_nl(self, query: str, limit: int = 10) -> list[dict]:
        """Semantic search for research reports via iwencai (requires IWENCAI_API_KEY).

        Falls back gracefully with an empty list if API key is not configured.
        """
        api_key = os.environ.get("IWENCAI_API_KEY", "")
        if not api_key:
            logger.info("iwencai search skipped: IWENCAI_API_KEY not set")
            return []
        base = os.environ.get("IWENCAI_BASE_URL", "https://openapi.iwencai.com")
        url = f"{base}/v1/report/search"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = requests.post(url, json={"query": query, "limit": limit}, headers=headers, timeout=30)
        if r.status_code != 200:
            logger.warning("iwencai search returned %s: %s", r.status_code, r.text[:200])
            return []
        data = r.json()
        return data.get("results", []) if isinstance(data, dict) else []
