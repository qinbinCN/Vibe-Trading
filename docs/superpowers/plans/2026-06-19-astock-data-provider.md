# AStockDataProvider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified `DataProvider` layer parallel to existing `Loader` layer, implementing `AStockDataProvider` that wraps all 27 endpoints from a-stock-data (simonlin1212/a-stock-data), plus 7 new Agent/MCP tools for research reports, news, announcements, stock profiles, financials, market signals, and capital flow.

**Architecture:** New `agent/backtest/data_providers/` package mirrors `loaders/` pattern — `base.py` defines `DataProviderProtocol`, `registry.py` provides `@register_provider` decorator and `PROVIDER_REGISTRY`. `astock.py` contains `AStockDataProvider` with all 27 endpoints. New tool classes in `agent/src/tools/` auto-discover via `BaseTool.__subclasses__()`. MCP tools registered in `mcp_server.py` via `@mcp.tool`.

**Tech Stack:** Python 3.11+, mootdx, requests, pandas, stockstats, FastMCP

---

### Task 1: DataProvider Protocol + Registry (Foundation)

**Files:**
- Create: `agent/backtest/data_providers/__init__.py`
- Create: `agent/backtest/data_providers/base.py`
- Create: `agent/backtest/data_providers/registry.py`

- [ ] **Step 1: Create package __init__.py**

```python
# agent/backtest/data_providers/__init__.py
"""Data providers for non-OHLCV market data (research reports, news, announcements, fundamentals, signals, capital flow)."""
```

- [ ] **Step 2: Create base.py with DataProviderProtocol**

```python
# agent/backtest/data_providers/base.py
"""DataProviderProtocol — interface for non-OHLCV data providers."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DataProviderProtocol(Protocol):
    """Interface that every non-OHLCV data provider must satisfy.

    Unlike DataLoaderProtocol (which is OHLCV-only with a fixed fetch()
    signature), providers expose domain-specific methods grouped by
    data category. The protocol only requires identification and
    availability checks.
    """

    name: str
    version: str

    def is_available(self) -> bool:
        """Check whether this data provider is usable (deps installed, etc.)."""
        ...

    def check_prerequisites(self) -> list[str]:
        """Return a list of missing dependencies (empty = all satisfied)."""
        ...
```

- [ ] **Step 3: Create registry.py with @register_provider**

```python
# agent/backtest/data_providers/registry.py
"""Provider registry — parallel to loaders/registry.py."""
from __future__ import annotations

import importlib
import logging
from typing import Any

from backtest.data_providers.base import DataProviderProtocol

logger = logging.getLogger(__name__)

PROVIDER_REGISTRY: dict[str, type[DataProviderProtocol]] = {}
"""Global provider registry, keyed by provider name."""

_provider_modules: list[str] = [
    "backtest.data_providers.astock",
]
"""Module paths to import during lazy discovery so @register_provider fires."""

_registration_done = False


def register_provider(cls: type[Any]) -> type[Any]:
    """Class decorator: register a provider into the global registry."""
    PROVIDER_REGISTRY[cls.name] = cls
    return cls


def _ensure_registered() -> None:
    """Lazy-import every known provider module so decorators fire."""
    global _registration_done
    if _registration_done:
        return
    for module_name in _provider_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            logger.debug("Skipped provider module %s: %s", module_name, exc)
    _registration_done = True


def get_provider(name: str) -> DataProviderProtocol | None:
    """Get a provider instance by name. Returns None if unavailable."""
    _ensure_registered()
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        return None
    try:
        instance = cls()
    except Exception as exc:
        logger.debug("Provider %s failed to construct: %s", name, exc)
        return None
    if instance.is_available():
        return instance
    return None


def list_providers() -> list[str]:
    """List all registered provider names (regardless of availability)."""
    _ensure_registered()
    return sorted(PROVIDER_REGISTRY.keys())


def list_available_providers() -> list[str]:
    """List provider names whose is_available() returns True."""
    _ensure_registered()
    result: list[str] = []
    for name, cls in PROVIDER_REGISTRY.items():
        try:
            instance = cls()
        except Exception:
            continue
        if instance.is_available():
            result.append(name)
    return sorted(result)
```

- [ ] **Step 4: Commit**

```bash
git add agent/backtest/data_providers/__init__.py agent/backtest/data_providers/base.py agent/backtest/data_providers/registry.py
git commit -m "feat: add DataProvider protocol and registry foundation"
```

---

### Task 2: AStockDataProvider — Infrastructure + Helper Functions

**Files:**
- Create: `agent/backtest/data_providers/astock.py` (partial, infrastructure first)

- [ ] **Step 1: Create file header, imports, and helper functions**

```python
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
throttling (≥1s interval + jitter) and session reuse. See upstream SKILL.md.
"""
from __future__ import annotations

import json
import logging
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
```

- [ ] **Step 2: Commit**

```bash
git add agent/backtest/data_providers/astock.py
git commit -m "feat: add AStockDataProvider infrastructure (em_get, datacenter, ticker helpers)"
```

---

### Task 3: AStockDataProvider — Layer 1: 行情 (3 endpoints)

**Files:**
- Modify: `agent/backtest/data_providers/astock.py` (append after helpers)

- [ ] **Step 1: Add mootdx K-line + Tencent quote + index methods**

Append to `astock.py`:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add agent/backtest/data_providers/astock.py
git commit -m "feat: AStockDataProvider Layer 1 — K-line + realtime quotes"
```

---

### Task 4: AStockDataProvider — Layer 2: 研报 (4 endpoints)

**Files:**
- Modify: `agent/backtest/data_providers/astock.py` (append inside class)

- [ ] **Step 1: Add research report methods inside class**

```python
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
        params = {
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
        # Extract JSON embedded in page
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
```

- [ ] **Step 2: Add missing import at top**

Insert after existing imports in `astock.py`:

```python
import os
```

- [ ] **Step 3: Commit**

```bash
git add agent/backtest/data_providers/astock.py
git commit -m "feat: AStockDataProvider Layer 2 — research reports + consensus EPS"
```

---

### Task 5: AStockDataProvider — Layer 3: 信号 (9 endpoints)

**Files:**
- Modify: `agent/backtest/data_providers/astock.py` (append inside class)

- [ ] **Step 1: Add signal layer methods inside class**

```python
    # ------------------------------------------------------------------
    # Layer 3: 信号 (9 endpoints) — THS hotspots + north-flow + eastmoney
    # ------------------------------------------------------------------

    def get_strong_stocks(self, concept: str = "") -> pd.DataFrame:
        """Fetch strong stocks with theme attribution via THS (零鉴权, ~73ms).

        Returns DataFrame with columns: code, name, change_pct, reason_tags
        """
        url = "https://eq.10jqka.com.cn/open/api/v1/stock/strong_stock/list"
        headers = {"User-Agent": UA, "Referer": "https://www.10jqka.com.cn/"}
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data.get("code") != 200:
            return pd.DataFrame()
        items = data.get("data", {}).get("list", [])
        rows = []
        for item in items:
            tags = item.get("reasonTags", [])
            rows.append({
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "change_pct": item.get("changePct"),
                "reason_tags": ",".join(t.get("reasonName", "") for t in tags) if tags else "",
            })
        if concept:
            rows = [r for r in rows if concept in r["reason_tags"]]
        return pd.DataFrame(rows)

    def get_north_flow(self, market: str = "hgt", date: str = "") -> pd.DataFrame:
        """Fetch north-bound minute-level capital flow via THS.

        Args:
            market: 'hgt' (沪股通) or 'sgt' (深股通)
            date: Date in YYYYMMDD format (default: today)
        Returns:
            DataFrame with columns: time, net_flow, buy_amount, sell_amount
        """
        if not date:
            date = time.strftime("%Y%m%d")
        url = f"https://push2his.eastmoney.com/api/qt/kamt.kline/get"
        params = {
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54",
            "klt": "1",
            "lmt": "500",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "market_id": "1" if market == "hgt" else "3",
            "beg": date, "end": date,
        }
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
        data = r.json()
        if not data.get("data") or not data["data"].get("klines"):
            return pd.DataFrame()
        rows = []
        for line in data["data"]["klines"]:
            parts = line.split(",")
            if len(parts) >= 4:
                rows.append({
                    "time": parts[0],
                    "net_flow": _safe_float(parts[1]),
                    "buy_amount": _safe_float(parts[2]),
                    "sell_amount": _safe_float(parts[3]),
                })
        return pd.DataFrame(rows)

    def get_concept_blocks(self, code: str) -> list[dict]:
        """Fetch stock concept/industry/regional block affiliations via EastMoney slist."""
        clean = _strip_code(code)
        url = "https://push2.eastmoney.com/api/qt/slist/get"
        params = {
            "spt": "3",
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f14,f3,f20",
            "secid": f"1.{clean}" if clean.startswith(("6", "9")) else f"0.{clean}",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        r = em_get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("data"):
            return []
        items = data["data"].get("slist", []) if isinstance(data["data"], dict) else data["data"]
        if not items:
            return []
        return [{"bk_code": item.get("f12"), "bk_name": item.get("f14"),
                  "change_pct": item.get("f3"), "market_cap": item.get("f20")}
                for item in items]

    def get_fund_flow_minute(self, code: str) -> pd.DataFrame:
        """Fetch minute-level fund flow via EastMoney push2.

        Returns DataFrame with: time, main_net, super_large_net, large_net, mid_net, small_net
        """
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "0", "klt": "1",
            "secid": f"{market}.{clean}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        r = em_get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("data") or not data["data"].get("klines"):
            return pd.DataFrame()
        rows = []
        for line in data["data"]["klines"]:
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append({
                    "time": parts[0],
                    "main_net": _safe_float(parts[1]),
                    "super_large_net": _safe_float(parts[3]),
                    "large_net": _safe_float(parts[2]),
                    "mid_net": _safe_float(parts[4]),
                    "small_net": _safe_float(parts[5]),
                })
        return pd.DataFrame(rows)

    def get_dragon_tiger_stock(self, code: str) -> list[dict]:
        """Fetch individual stock dragon-tiger board appearance records."""
        clean = _strip_code(code)
        return eastmoney_datacenter(
            report_name="RPT_DAILY_BILLBOARDTRADINGDETAILS",
            filter_str=f'(SECURITY_CODE="{clean}")',
            sort_columns="TRADE_DATE", sort_types="-1",
            page_size=50,
        )

    def get_dragon_tiger_market(self, date: str = "") -> pd.DataFrame:
        """Fetch market-wide dragon-tiger board rankings for a date.

        Args:
            date: YYYY-MM-DD format (default: latest trading day)
        """
        if not date:
            date = time.strftime("%Y-%m-%d")
        data = eastmoney_datacenter(
            report_name="RPT_DAILY_BILLBOARDTRADING",
            filter_str=f'(TRADE_DATE>="{date}")',
            sort_columns="NET_BUY_AMOUNT", sort_types="-1",
            page_size=200,
        )
        return pd.DataFrame(data) if data else pd.DataFrame()

    def get_unlock_calendar(self, code: str = "", days: int = 90) -> list[dict]:
        """Fetch restricted-share unlock calendar.

        Args:
            code: Optional stock code filter
            days: Days ahead to check (default 90)
        """
        from datetime import datetime, timedelta
        end_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        filters = []
        if code:
            filters.append(f'(SECURITY_CODE="{_strip_code(code)}")')
        return eastmoney_datacenter(
            report_name="RPT_LIFT_RESTRICTEDSHARES",
            columns="SECURITY_CODE,SECURITY_NAME,LIFT_DATE,LIFT_SHARES,LIFT_MARKET_CAP",
            filter_str=" AND ".join(filters) if filters else "",
            sort_columns="LIFT_DATE", sort_types="1",
            page_size=200,
        )

    def get_sector_ranking(self) -> pd.DataFrame:
        """Fetch EastMoney sector/industry ranking (涨跌排名)."""
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "200",
            "po": "1", "np": "1",
            "fltt": "2", "invt": "2",
            "fid": "f3", "fs": "m:90+t:2",
            "fields": "f2,f3,f4,f12,f14,f104,f105",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        r = em_get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("data") or not data["data"].get("diff"):
            return pd.DataFrame()
        items = data["data"]["diff"]
        rows = [{"code": item.get("f12"), "name": item.get("f14"),
                  "change_pct": item.get("f3"), "up_count": item.get("f104"),
                  "down_count": item.get("f105")} for item in items]
        return pd.DataFrame(rows)

    def get_theme_attribution(self) -> list[dict]:
        """Fetch current market theme/concept hotspot attribution via THS."""
        url = "https://eq.10jqka.com.cn/open/api/v1/stock/concept/list"
        headers = {"User-Agent": UA, "Referer": "https://www.10jqka.com.cn/"}
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data.get("code") != 200:
            return []
        items = data.get("data", {}).get("list", [])
        return [{"concept_name": item.get("conceptName", ""),
                  "change_pct": item.get("changePct"),
                  "lead_stock": item.get("leadStock", ""),
                  "reason": item.get("reason", "")} for item in items]
```

- [ ] **Step 2: Commit**

```bash
git add agent/backtest/data_providers/astock.py
git commit -m "feat: AStockDataProvider Layer 3 — market signals (9 endpoints)"
```

---

### Task 6: AStockDataProvider — Layer 4-7: 资金面, 新闻, 基础数据, 公告 (12 endpoints)

**Files:**
- Modify: `agent/backtest/data_providers/astock.py` (append inside class)

- [ ] **Step 1: Add remaining 12 endpoint methods inside class**

```python
    # ------------------------------------------------------------------
    # Layer 4: 资金面 / 筹码 (5 endpoints)
    # ------------------------------------------------------------------

    def get_margin_trading(self, code: str, start_date: str = "", end_date: str = "") -> list[dict]:
        """Fetch margin trading (融资融券) daily details."""
        filters = [f'(SECURITY_CODE="{_strip_code(code)}")']
        if start_date:
            filters.append(f'(TRADE_DATE>="{start_date}")')
        if end_date:
            filters.append(f'(TRADE_DATE<="{end_date}")')
        return eastmoney_datacenter(
            report_name="RPT_MARGIN_TRADINGDETAIL",
            filter_str=" AND ".join(filters),
            sort_columns="TRADE_DATE", sort_types="-1",
            page_size=200,
        )

    def get_block_trades(self, code: str, start_date: str = "", end_date: str = "") -> list[dict]:
        """Fetch block trade (大宗交易) records with buyer/seller brokerage names."""
        filters = [f'(SECURITY_CODE="{_strip_code(code)}")']
        if start_date:
            filters.append(f'(TRADE_DATE>="{start_date}")')
        if end_date:
            filters.append(f'(TRADE_DATE<="{end_date}")')
        return eastmoney_datacenter(
            report_name="RPT_BLOCKTRADE",
            filter_str=" AND ".join(filters),
            sort_columns="TRADE_DATE", sort_types="-1",
            page_size=100,
        )

    def get_shareholder_changes(self, code: str) -> list[dict]:
        """Fetch quarterly shareholder count changes (筹码集中度)."""
        return eastmoney_datacenter(
            report_name="RPT_F10_FINANCE_SHAREHOLDERNUMBER",
            filter_str=f'(SECURITY_CODE="{_strip_code(code)}")',
            sort_columns="END_DATE", sort_types="-1",
            page_size=50,
        )

    def get_dividend_history(self, code: str) -> list[dict]:
        """Fetch dividend/split history (分红送转)."""
        return eastmoney_datacenter(
            report_name="RPT_F10_FINANCE_DIVIDEND",
            filter_str=f'(SECURITY_CODE="{_strip_code(code)}")',
            sort_columns="EQUITY_DATE", sort_types="-1",
            page_size=50,
        )

    def get_fund_flow_120d(self, code: str) -> pd.DataFrame:
        """Fetch 120-day daily-level major/retail fund flow via EastMoney push2his."""
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "120", "klt": "101",
            "secid": f"{market}.{clean}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        r = em_get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("data") or not data["data"].get("klines"):
            return pd.DataFrame()
        rows = []
        for line in data["data"]["klines"]:
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append({
                    "date": parts[0],
                    "main_net": _safe_float(parts[1]),
                    "super_large_net": _safe_float(parts[3]),
                    "large_net": _safe_float(parts[2]),
                    "mid_net": _safe_float(parts[4]),
                    "small_net": _safe_float(parts[5]),
                })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Layer 5: 新闻 (2 endpoints)
    # ------------------------------------------------------------------

    def get_stock_news(self, code: str, limit: int = 20) -> list[dict]:
        """Fetch stock-specific news via EastMoney search-api-web.

        Returns list of dicts with: title, url, publish_time, source
        """
        clean = _strip_code(code)
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        params = {
            "cb": "jQuery",
            "param": json.dumps({
                "uid": "",
                "keyword": clean,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "pageIndex": 1,
                "pageSize": limit,
            }),
            "_": str(int(time.time() * 1000)),
        }
        r = requests.get(url, params=params, headers={"User-Agent": UA, "Referer": "https://www.eastmoney.com/"}, timeout=15)
        text = r.text
        m = re.search(r"jQuery\((.*)\)", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(1))
        articles = data.get("result", {}).get("cmsArticleWebOld", [])
        if isinstance(articles, dict):
            articles = list(articles.values())
        if not isinstance(articles, list):
            return []
        return [{"title": a.get("title", ""), "url": a.get("url", ""),
                  "publish_time": a.get("publishTime", ""), "source": a.get("source", "")}
                for a in articles[:limit]]

    def get_global_news(self, limit: int = 50) -> list[dict]:
        """Fetch 7x24 global financial news via EastMoney np-weblist (replaces defunct cls.cn)."""
        url = "https://np-weblist.eastmoney.com/comm/web/getNews"
        params = {"client": "pc_web", "limit": str(limit)}
        r = requests.get(url, params=params, headers={"User-Agent": UA, "Referer": "https://www.eastmoney.com/"}, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("list", []) if isinstance(data, dict) else []
        return [{"title": item.get("title", ""), "url": item.get("url", ""),
                  "publish_time": item.get("showTime", ""), "summary": item.get("summary", "")}
                for item in items]

    # ------------------------------------------------------------------
    # Layer 6: 基础数据 (4 endpoints)
    # ------------------------------------------------------------------

    def get_financial_snapshot(self, code: str) -> dict[str, Any]:
        """Fetch quarterly financial snapshot (37 fields) via mootdx finance.

        Returns dict with: EPS, ROE, net_profit, revenue, total_assets, etc.
        """
        from mootdx.finance import Finance
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        client = Finance.factory(market="std")
        df = client.finance(symbol=clean, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[-1].to_dict()
        return {str(k).lower(): v for k, v in row.items()}

    def get_company_f10(self, code: str, category: str = "gszl") -> str:
        """Fetch F10 company profile data via mootdx.

        Args:
            category: 'gszl'(公司资料), 'gdbd'(股东变动), 'cwbl'(财务比率),
                      'gsgg'(公司公告), 'yjbg'(业绩报告), 'zcfzb'(资产负债表),
                      'lrb'(利润表), 'xjllb'(现金流量表), 'gdhs'(股东户数)
        """
        from mootdx.finance import Finance
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        client = Finance.factory(market="std")
        return client.F10(symbol=clean, market=market, category=category)

    def get_stock_info(self, code: str) -> dict[str, Any]:
        """Fetch stock basic info (industry, shares, market cap, listing date) via EastMoney push2."""
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": f"{market}.{clean}",
            "fields": "f57,f58,f73,f74,f75,f76,f77,f78,f79,f80,f81,f82,f83,f84,f85,f86,f87,f88,f89,f90,f91,f92",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        r = em_get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("data"):
            return {}
        d = data["data"]
        return {
            "code": d.get("f57"), "name": d.get("f58"),
            "industry": d.get("f100"), "total_shares": d.get("f84"),
            "float_shares": d.get("f85"), "market_cap": d.get("f116"),
            "listing_date": d.get("f95"),
        }

    def get_financial_statements(self, code: str, report_type: str = "balance_sheet") -> pd.DataFrame:
        """Fetch financial statements (balance sheet / income / cashflow) via Sina Finance.

        Args:
            report_type: 'balance_sheet', 'income_statement', or 'cashflow'
        Returns:
            DataFrame with rows as reporting periods, columns as line items.
        """
        clean = _strip_code(code)
        type_map = {"balance_sheet": "zcfzb", "income_statement": "lrb", "cashflow": "xjllb"}
        sina_type = type_map.get(report_type, "zcfzb")
        url = f"https://quotes.sina.cn/cn/api/json_v2.php/data/CN_STOCK_A_{sina_type}"
        params = {"symbol": clean}
        r = requests.get(url, params=params, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}, timeout=15)
        data = r.json()
        if not data.get("result") or not data["result"].get("data"):
            return pd.DataFrame()
        report_list = data["result"]["data"].get("report_list", {})
        all_rows: list[dict] = []
        for period, period_data in sorted(report_list.items()):
            row_data = period_data.get("data", []) if isinstance(period_data, dict) else []
            row: dict[str, Any] = {"period": period}
            for item in row_data:
                row[item.get("item_title", "")] = item.get("item_value")
            all_rows.append(row)
        return pd.DataFrame(all_rows)

    # ------------------------------------------------------------------
    # Layer 7: 公告 (1 endpoint)
    # ------------------------------------------------------------------

    def get_announcements(self, code: str = "", keyword: str = "",
                           start_date: str = "", end_date: str = "",
                           page: int = 1, page_size: int = 30) -> list[dict]:
        """Fetch A-share announcements via CNInfo (巨潮).

        Args:
            code: Optional stock code filter
            keyword: Optional full-text keyword filter
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
        Returns:
            List of dicts with: title, url, publish_date, stock_code, stock_name, pdf_url
        """
        org_id = None
        if code:
            clean = _strip_code(code)
            org_id = self._cninfo_orgid(clean)

        params: dict[str, Any] = {
            "pageNum": str(page), "pageSize": str(page_size),
            "column": "szse", "plate": "sz;sh;bj",
            "searchDate": "",
            "isHLtitle": "true",
        }
        if org_id:
            params["stock"] = f"{org_id},{_strip_code(code)}"
        if keyword:
            params["seDate"] = ""
            r = requests.post(
                "https://www.cninfo.com.cn/new/fulltextSearch/full",
                data={"searchkey": keyword, "sdate": start_date or "", "edate": end_date or "",
                       "isfulltext": "true", "sortName": "pubdate", "sortType": "desc",
                       "pageNum": str(page), "pageSize": str(page_size)},
                headers={"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/"},
                timeout=15,
            )
            data = r.json()
            records = data.get("announcements", []) if isinstance(data, dict) else []
            return [{"title": rec.get("announcementTitle", ""),
                      "url": f"https://www.cninfo.com.cn/new/disclosure/detail?announcementId={rec.get('announcementId', '')}",
                      "publish_date": rec.get("announcementTime", ""),
                      "stock_code": rec.get("secCode", ""),
                      "stock_name": rec.get("secName", "")} for rec in records[:page_size]]
        elif code and org_id:
            r = requests.post(
                "https://www.cninfo.com.cn/new/disclosure",
                data={"stock": f"{org_id},{_strip_code(code)}", "pageNum": str(page), "pageSize": str(page_size),
                       "column": "szse", "plate": "sz;sh;bj"},
                headers={"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/"},
                timeout=15,
            )
            data = r.json()
            records = data.get("classifiedAnnouncements", []) if isinstance(data, dict) else []
            return [{"title": rec.get("announcementTitle", ""),
                      "url": f"https://www.cninfo.com.cn/new/disclosure/detail?announcementId={rec.get('announcementId', '')}",
                      "publish_date": rec.get("announcementTime", ""),
                      "stock_code": rec.get("secCode", ""),
                      "stock_name": rec.get("secName", "")} for rec in records[:page_size]]
        return []

    # OrgId cache for cninfo
    _orgid_cache: dict[str, str] = {}

    def _cninfo_orgid(self, code: str) -> str:
        """Fetch CNInfo orgId for a stock code (cached at module level)."""
        if code in self._orgid_cache:
            return self._orgid_cache[code]
        try:
            url = "https://www.cninfo.com.cn/new/data/szse_stock.json"
            r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            data = r.json()
            for item in data.get("stockList", []):
                if item.get("code") == code:
                    org_id = str(item.get("orgId", ""))
                    self._orgid_cache[code] = org_id
                    return org_id
        except Exception:
            pass
        fallback = f"gssz{code}" if code.startswith(("0", "3", "8")) else f"gssh{code}"
        self._orgid_cache[code] = fallback
        return fallback
```

- [ ] **Step 2: Commit**

```bash
git add agent/backtest/data_providers/astock.py
git commit -m "feat: AStockDataProvider Layers 4-7 — capital flow, news, fundamentals, announcements"
```

---

### Task 7: Update Dependencies

**Files:**
- Modify: `agent/requirements.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add mootdx and stockstats to requirements.txt**

Append to `agent/requirements.txt`:

```
# A-stock data provider (a-stock-data)
mootdx>=0.10
stockstats
```

- [ ] **Step 2: Add to pyproject.toml dependencies**

In `pyproject.toml`, after the existing dependencies block (around line 28-30), add:

```toml
    "mootdx>=0.10",
    "stockstats",
```

- [ ] **Step 3: Commit**

```bash
git add agent/requirements.txt pyproject.toml
git commit -m "chore: add mootdx and stockstats dependencies for a-stock-data integration"
```

---

### Task 8: Agent Tool — get_stock_profile (基础面查询)

**Files:**
- Create: `agent/src/tools/astock_profile_tool.py`

- [ ] **Step 1: Create StockProfileTool**

```python
# agent/src/tools/astock_profile_tool.py
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
```

- [ ] **Step 2: Commit**

```bash
git add agent/src/tools/astock_profile_tool.py
git commit -m "feat: add get_stock_profile agent tool"
```

---

### Task 9: Agent Tools — get_research_reports, get_stock_news, get_announcements, get_stock_financials

**Files:**
- Create: `agent/src/tools/astock_report_tool.py`
- Create: `agent/src/tools/astock_news_tool.py`
- Create: `agent/src/tools/astock_announcement_tool.py`
- Create: `agent/src/tools/astock_financials_tool.py`

- [ ] **Step 1: Create ResearchReportTool**

```python
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
```

- [ ] **Step 2: Create StockNewsTool**

```python
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
```

- [ ] **Step 3: Create AnnouncementTool**

```python
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
```

- [ ] **Step 4: Create StockFinancialsTool**

```python
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
```

- [ ] **Step 5: Commit**

```bash
git add agent/src/tools/astock_report_tool.py agent/src/tools/astock_news_tool.py agent/src/tools/astock_announcement_tool.py agent/src/tools/astock_financials_tool.py
git commit -m "feat: add research reports, news, announcements, financials agent tools"
```

---

### Task 10: Agent Tools — get_market_signals, get_capital_flow

**Files:**
- Create: `agent/src/tools/astock_signals_tool.py`
- Create: `agent/src/tools/astock_capital_tool.py`

- [ ] **Step 1: Create MarketSignalsTool**

```python
# agent/src/tools/astock_signals_tool.py
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
        "2) North-bound capital flow minute-level (via push2his), "
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
            "market": {"type": "string", "description": "For north_flow: 'hgt' (沪股通) or 'sgt' (深股通)", "default": "hgt"},
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
```

- [ ] **Step 2: Create CapitalFlowTool**

```python
# agent/src/tools/astock_capital_tool.py
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
```

- [ ] **Step 3: Commit**

```bash
git add agent/src/tools/astock_signals_tool.py agent/src/tools/astock_capital_tool.py
git commit -m "feat: add market signals and capital flow agent tools"
```

---

### Task 11: MCP Server Tool Registration

**Files:**
- Modify: `agent/mcp_server.py` (add new MCP tool functions)

- [ ] **Step 1: Add MCP tool functions to mcp_server.py**

In `agent/mcp_server.py`, locate the section after the last `@mcp.tool` function. Append the following 7 MCP tools. Each delegates to the corresponding provider method directly (not via `registry.execute()` for simplicity, since these are new tools that don't exist in the local agent registry pattern).

Find the import section at the top of `mcp_server.py` and add:

```python
from backtest.data_providers.registry import get_provider
```

Then add the 7 MCP tools (use the exact `@mcp.tool` pattern from existing tools):

```python
@mcp.tool
def get_research_reports(
    code: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """Search A-share research reports from EastMoney + THS consensus EPS.

    Args:
        code: Stock code filter, e.g. 600519 (optional).
        keyword: Keyword search (optional).
        page: Page number, 1-based.
        page_size: Results per page.
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    result = {"reports": provider.get_research_reports(code=code, keyword=keyword, page=page, page_size=page_size)}
    if code:
        try:
            result["consensus_eps"] = provider.get_consensus_eps(code)
        except Exception:
            pass
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool
def get_stock_news(code: str = "", global_news: bool = False, limit: int = 20) -> str:
    """Fetch A-share stock news and/or global 7x24 financial news.

    Args:
        code: Stock code for individual stock news (optional).
        global_news: Also fetch global 7x24 news headlines.
        limit: Max entries per category.
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    result: dict[str, Any] = {}
    if code:
        result["stock_news"] = provider.get_stock_news(code, limit=limit)
    if global_news:
        result["global_news"] = provider.get_global_news(limit=limit)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool
def get_announcements(
    code: str = "",
    keyword: str = "",
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 30,
) -> str:
    """Search A-share stock announcements via CNInfo (巨潮资讯网).

    Args:
        code: Stock code filter (optional).
        keyword: Full-text keyword search (optional).
        start_date: Start date YYYY-MM-DD (optional).
        end_date: End date YYYY-MM-DD (optional).
        page: Page number.
        page_size: Results per page.
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    announcements = provider.get_announcements(
        code=code, keyword=keyword, start_date=start_date, end_date=end_date,
        page=page, page_size=page_size,
    )
    return json.dumps({"announcements": announcements, "count": len(announcements)}, ensure_ascii=False, indent=2, default=str)


@mcp.tool
def get_stock_profile(code: str, f10_category: str = "gszl") -> str:
    """Fetch A-share stock fundamental profile (financial snapshot, F10, basic info).

    Args:
        code: Stock code, e.g. 600519, 000001.
        f10_category: F10 category — gszl(公司资料), gdbd(股东变动), cwbl(财务比率), gsgg(公司公告).
    """
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
        result["f10"] = provider.get_company_f10(code, category=f10_category)
    except Exception as exc:
        result["f10_error"] = str(exc)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool
def get_stock_financials(code: str, report_type: str = "balance_sheet") -> str:
    """Fetch A-share financial statements (balance sheet, income, cashflow) from Sina.

    Args:
        code: Stock code, e.g. 600519.
        report_type: balance_sheet, income_statement, or cashflow.
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    df = provider.get_financial_statements(code, report_type=report_type)
    return json.dumps(
        {"code": code, "report_type": report_type, "data": df.to_dict(orient="records") if not df.empty else [], "row_count": len(df)},
        ensure_ascii=False, indent=2, default=str,
    )


@mcp.tool
def get_market_signals(signal_type: str, code: str = "", market: str = "hgt", date: str = "", days: int = 90) -> str:
    """Fetch A-share market signals: strong stocks, north flow, dragon-tiger, unlock calendar, sector ranking, themes.

    Args:
        signal_type: One of strong_stocks, north_flow, dragon_tiger_stock, dragon_tiger_market,
                     unlock_calendar, sector_ranking, theme_attribution, concept_blocks.
        code: Stock code (required for some signal types).
        market: For north_flow — 'hgt' (沪股通) or 'sgt' (深股通).
        date: Date YYYY-MM-DD (for dragon_tiger_market).
        days: Days ahead for unlock_calendar.
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    result: dict[str, Any] = {"signal_type": signal_type}
    if signal_type == "strong_stocks":
        result["data"] = provider.get_strong_stocks().to_dict(orient="records")
    elif signal_type == "north_flow":
        result["data"] = provider.get_north_flow(market=market).to_dict(orient="records")
    elif signal_type == "dragon_tiger_stock":
        result["data"] = provider.get_dragon_tiger_stock(code)
    elif signal_type == "dragon_tiger_market":
        result["data"] = provider.get_dragon_tiger_market(date=date).to_dict(orient="records")
    elif signal_type == "unlock_calendar":
        result["data"] = provider.get_unlock_calendar(code=code, days=days)
    elif signal_type == "sector_ranking":
        result["data"] = provider.get_sector_ranking().to_dict(orient="records")
    elif signal_type == "theme_attribution":
        result["data"] = provider.get_theme_attribution()
    elif signal_type == "concept_blocks":
        result["data"] = provider.get_concept_blocks(code)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool
def get_capital_flow(flow_type: str, code: str, start_date: str = "", end_date: str = "") -> str:
    """Fetch A-share capital flow data: margin, block trades, shareholder changes, dividend, fund flow.

    Args:
        flow_type: One of margin, block_trades, shareholder, dividend, fund_flow_120d.
        code: Stock code.
        start_date: Start date YYYY-MM-DD (optional).
        end_date: End date YYYY-MM-DD (optional).
    """
    provider = get_provider("astock")
    if provider is None:
        return json.dumps({"status": "error", "error": "AStockDataProvider unavailable"}, ensure_ascii=False)
    result: dict[str, Any] = {"flow_type": flow_type, "code": code}
    if flow_type == "margin":
        result["data"] = provider.get_margin_trading(code, start_date=start_date, end_date=end_date)
    elif flow_type == "block_trades":
        result["data"] = provider.get_block_trades(code, start_date=start_date, end_date=end_date)
    elif flow_type == "shareholder":
        result["data"] = provider.get_shareholder_changes(code)
    elif flow_type == "dividend":
        result["data"] = provider.get_dividend_history(code)
    elif flow_type == "fund_flow_120d":
        result["data"] = provider.get_fund_flow_120d(code).to_dict(orient="records")
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
```

Also ensure `import json` and `from typing import Any` are present at the top of `mcp_server.py`. Check existing imports and add if needed.

- [ ] **Step 2: Commit**

```bash
git add agent/mcp_server.py
git commit -m "feat: register 7 a-stock-data MCP tools in mcp_server"
```

---

### Task 12: Smoke Test + Verification

**Files:**
- Create: `agent/tests/test_astock_provider.py` (smoke test)

- [ ] **Step 1: Create smoke test**

```python
# agent/tests/test_astock_provider.py
"""Smoke tests for AStockDataProvider — verifies registration, availability, and endpoint signatures."""
from __future__ import annotations

import pytest


@pytest.fixture
def provider():
    from backtest.data_providers.registry import get_provider, _ensure_registered
    _ensure_registered()
    return get_provider("astock")


class TestProviderRegistration:
    def test_provider_registered(self):
        from backtest.data_providers.registry import PROVIDER_REGISTRY, _ensure_registered
        _ensure_registered()
        assert "astock" in PROVIDER_REGISTRY, "AStockDataProvider should be registered"

    def test_provider_listable(self):
        from backtest.data_providers.registry import list_providers
        assert "astock" in list_providers()


class TestProviderAvailability:
    def test_check_prerequisites(self, provider):
        missing = provider.check_prerequisites() if provider else ["mootdx"]
        # mootdx may or may not be installed; just verify it returns a list
        assert isinstance(missing, list)

    def test_is_available(self, provider):
        if provider is None:
            pytest.skip("Provider not available (mootdx not installed)")
        result = provider.is_available()
        assert isinstance(result, bool)


class TestEndpointSignatures:
    """Verify all 27 endpoints are callable with correct signatures — no network calls."""

    def test_endpoints_exist(self, provider):
        if provider is None:
            pytest.skip("Provider not available")
        expected = [
            "get_kline", "get_realtime_quote", "get_index_quote",
            "get_research_reports", "download_report_pdf", "get_consensus_eps", "search_reports_nl",
            "get_strong_stocks", "get_north_flow", "get_concept_blocks",
            "get_fund_flow_minute", "get_dragon_tiger_stock", "get_dragon_tiger_market",
            "get_unlock_calendar", "get_sector_ranking", "get_theme_attribution",
            "get_margin_trading", "get_block_trades", "get_shareholder_changes",
            "get_dividend_history", "get_fund_flow_120d",
            "get_stock_news", "get_global_news",
            "get_financial_snapshot", "get_company_f10", "get_stock_info", "get_financial_statements",
            "get_announcements",
        ]
        for name in expected:
            assert hasattr(provider, name), f"Missing endpoint: {name}"
            assert callable(getattr(provider, name)), f"Not callable: {name}"
```

- [ ] **Step 2: Run smoke test**

```bash
cd D:/project/git/Vibe-Trading/agent && python -m pytest tests/test_astock_provider.py -v --no-header 2>&1
```

Expected: Provider registration + availability + endpoint existence tests pass. Network tests are not included in smoke test.

- [ ] **Step 3: Verify Agent Tool Auto-discovery**

```bash
cd D:/project/git/Vibe-Trading/agent && python -c "
from src.agent.tools import BaseTool
from src.tools.__init__ import _discover_subclasses
tools = [t.name for t in _discover_subclasses() if t.name]
expected = ['get_stock_profile', 'get_research_reports', 'get_stock_news', 'get_announcements', 'get_stock_financials', 'get_market_signals', 'get_capital_flow']
for e in expected:
    status = 'OK' if e in tools else 'MISSING'
    print(f'{status}: {e}')
"
```

Expected: All 7 tools show `OK`.

- [ ] **Step 4: Install dependencies**

```bash
pip install mootdx stockstats
```

- [ ] **Step 5: Live smoke test (optional, requires mainland IP)**

```bash
cd D:/project/git/Vibe-Trading/agent && python -c "
from backtest.data_providers.registry import get_provider
p = get_provider('astock')
if p:
    # Test quote (Tencent — works from any IP)
    q = p.get_realtime_quote(['600519'])
    print('Quote test:', 'OK' if q else 'EMPTY')
    print(q.get('600519', {}))
else:
    print('Provider not available')
"
```

- [ ] **Step 6: Commit**

```bash
git add agent/tests/test_astock_provider.py
git commit -m "test: add AStockDataProvider smoke tests"
```

---

## Plan Summary

| Task | Files | What |
|------|-------|------|
| 1 | `data_providers/__init__.py`, `base.py`, `registry.py` | Protocol + registration |
| 2 | `data_providers/astock.py` (①) | Infrastructure: em_get, ticker helpers |
| 3 | `data_providers/astock.py` (②) | Layer 1: 行情 (3 endpoints) |
| 4 | `data_providers/astock.py` (③) | Layer 2: 研报 (4 endpoints) |
| 5 | `data_providers/astock.py` (④) | Layer 3: 信号 (9 endpoints) |
| 6 | `data_providers/astock.py` (⑤) | Layers 4-7: 资金面/新闻/基础/公告 (12 endpoints) |
| 7 | `requirements.txt`, `pyproject.toml` | 依赖更新 |
| 8 | `src/tools/astock_profile_tool.py` | Tool: get_stock_profile |
| 9 | 4 tool files | Tools: reports, news, announcements, financials |
| 10 | 2 tool files | Tools: signals, capital flow |
| 11 | `mcp_server.py` | 7 MCP tool registrations |
| 12 | `tests/test_astock_provider.py` | Smoke test + verification |
