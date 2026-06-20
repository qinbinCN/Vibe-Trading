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
from pathlib import Path
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
    # Local gpcw cache (shared across instances)
    # ------------------------------------------------------------------
    _gpcw_df: pd.DataFrame | None = None
    _gpcw_report_date: str | None = None
    _gpcw_field_names: list[str] | None = None

    @classmethod
    def _load_gpcw_local(cls) -> pd.DataFrame | None:
        """Load the latest gpcw zip from TDX_ROOT_PATH (cached at class level)."""
        if cls._gpcw_df is not None:
            return cls._gpcw_df

        tdx_root = os.environ.get("TDX_ROOT_PATH", "").strip()
        if not tdx_root:
            return None
        cw_dir = Path(tdx_root) / "vipdoc" / "cw"
        if not cw_dir.is_dir():
            return None

        try:
            from pytdx.reader import HistoryFinancialReader
        except ImportError:
            return None

        zips = sorted(cw_dir.glob("gpcw*.zip"), reverse=True)
        for z in zips:
            if z.stat().st_size > 5000:
                break
        else:
            return None

        try:
            reader = HistoryFinancialReader()
            df = reader.get_df(str(z))
            if df is not None and not df.empty:
                # Apply Chinese field-name mapping
                field_names = cls._get_gpcw_field_names()
                if field_names:
                    numeric_cols = [c for c in df.columns if c.startswith("col")]
                    rename = {}
                    for c in numeric_cols:
                        col_num = int(c.replace("col", ""))
                        if col_num < len(field_names):
                            rename[c] = field_names[col_num]
                    df = df.rename(columns=rename)
                cls._gpcw_df = df
                cls._gpcw_report_date = str(
                    df["report_date"].iloc[0]
                    if "report_date" in df.columns
                    else "unknown"
                )
                logger.info(
                    "astock: loaded local gpcw from %s — %d stocks, %d fields",
                    z.name, df.shape[0], df.shape[1],
                )
        except Exception as exc:
            logger.warning("astock: failed to load local gpcw: %s", exc)

        return cls._gpcw_df

    @staticmethod
    def _get_gpcw_field_names() -> list[str]:
        """Load the Chinese field-name list from mootdx's columns.py.

        Done without ``import mootdx`` to avoid its tdxpy import requirement.
        Returns an empty list when the file cannot be found or parsed.
        """
        if AStockDataProvider._gpcw_field_names is not None:
            return AStockDataProvider._gpcw_field_names

        import ast
        import site

        for site_dir in site.getsitepackages():
            col_path = Path(site_dir) / "mootdx" / "financial" / "columns.py"
            if col_path.is_file():
                break
        else:
            AStockDataProvider._gpcw_field_names = []
            return []

        try:
            raw = col_path.read_bytes()
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                AStockDataProvider._gpcw_field_names = []
                return []

            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "columns":
                            AStockDataProvider._gpcw_field_names = ast.literal_eval(
                                node.value
                            )
                            logger.debug(
                                "astock: loaded %d gpcw field names from mootdx",
                                len(AStockDataProvider._gpcw_field_names),
                            )
                            return AStockDataProvider._gpcw_field_names
        except Exception as exc:
            logger.warning("astock: failed to load gpcw field names: %s", exc)

        AStockDataProvider._gpcw_field_names = []
        return []

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        # Check mootdx (online TCP)
        try:
            import mootdx  # noqa: F401
            return True
        except ImportError:
            pass
        # Check pytdx + local TDX path
        try:
            import pytdx  # noqa: F401
            tdx_root = os.environ.get("TDX_ROOT_PATH", "").strip()
            if tdx_root and Path(tdx_root).is_dir():
                return True
        except ImportError:
            pass
        return False

    def check_prerequisites(self) -> list[str]:
        missing = []
        has_mootdx = False
        try:
            import mootdx  # noqa: F401
            has_mootdx = True
        except ImportError:
            pass

        has_pytdx = False
        try:
            import pytdx  # noqa: F401
            has_pytdx = True
        except ImportError:
            pass

        has_tdx_path = bool(
            os.environ.get("TDX_ROOT_PATH", "").strip()
            and Path(os.environ["TDX_ROOT_PATH"].strip()).is_dir()
        )

        if not has_mootdx and not (has_pytdx and has_tdx_path):
            missing.append("mootdx>=0.10 (or pytdx + TDX_ROOT_PATH)")
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
        """Fetch index/ETF quotes via Tencent. Accepts raw codes like ['000001', '399006'] (same as get_realtime_quote)."""
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
            params["filter"] = f'(SECURITY_CODE="{_strip_code(code)}")'
        if keyword:
            params["keyword"] = keyword
        r = em_get("https://reportapi.eastmoney.com/report/list", params=params, timeout=15)
        d = r.json()
        if d.get("result") and d["result"].get("data"):
            return d["result"]["data"]
        return []

    def download_report_pdf(self, url: str, save_path: str = "") -> bytes:
        """Download a research report PDF from EastMoney URL."""
        r = em_get(url, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=30)
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

    # ------------------------------------------------------------------
    # Layer 3: 信号 (9 endpoints) — THS hotspots + north-flow + eastmoney
    # ------------------------------------------------------------------

    def get_strong_stocks(self, concept: str = "") -> pd.DataFrame:
        """Fetch strong stocks with theme attribution via THS.

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
        """Fetch north-bound minute-level capital flow.

        Args:
            market: 'hgt' (沪股通) or 'sgt' (深股通)
            date: Date in YYYYMMDD format (default: today)
        """
        if not date:
            date = time.strftime("%Y%m%d")
        url = "https://push2his.eastmoney.com/api/qt/kamt.kline/get"
        params = {
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54",
            "klt": "1", "lmt": "500",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "market_id": "1" if market == "hgt" else "3",
            "beg": date, "end": date,
        }
        r = em_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
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
            "spt": "3", "fltt": "2", "invt": "2",
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
        """Fetch minute-level fund flow via EastMoney push2."""
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
        """Fetch market-wide dragon-tiger board rankings for a date."""
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
        """Fetch restricted-share unlock calendar."""
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
        """Fetch EastMoney sector/industry ranking."""
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
        r = em_get(url, params=params, headers={"User-Agent": UA, "Referer": "https://www.eastmoney.com/"}, timeout=15)
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
        """Fetch 7x24 global financial news via EastMoney np-weblist."""
        url = "https://np-weblist.eastmoney.com/comm/web/getNews"
        params = {"client": "pc_web", "limit": str(limit)}
        r = em_get(url, params=params, headers={"User-Agent": UA, "Referer": "https://www.eastmoney.com/"}, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("list", []) if isinstance(data, dict) else []
        return [{"title": item.get("title", ""), "url": item.get("url", ""),
                  "publish_time": item.get("showTime", ""), "summary": item.get("summary", "")}
                for item in items]

    # ------------------------------------------------------------------
    # Layer 6: 基础数据 (4 endpoints)
    # ------------------------------------------------------------------

    def get_financial_snapshot(self, code: str) -> dict[str, Any]:
        """Fetch latest financial snapshot — local gpcw first, mootdx TCP fallback.

        When ``TDX_ROOT_PATH`` is configured, reads the newest gpcw zip locally
        (584 fields with Chinese names).  Otherwise falls back to the mootdx TCP
        protocol for a 37-field snapshot.
        """
        # Try local gpcw first
        gpcw = self._load_gpcw_local()
        if gpcw is not None:
            clean = _strip_code(code)
            if clean in gpcw.index:
                row = gpcw.loc[clean]
                # Convert to dict, filtering out NaN and keeping reasonable values
                result: dict[str, Any] = {}
                for col_name, val in row.items():
                    if col_name == "report_date":
                        result[col_name] = str(val)
                        continue
                    try:
                        fv = float(val)
                        if pd.isna(fv):
                            continue
                        result[col_name] = fv
                    except (ValueError, TypeError):
                        continue
                result["_source"] = "tdx_local"
                result["_report_date"] = self._gpcw_report_date
                return result

        # Fallback: mootdx TCP (online)
        from mootdx.finance import Finance
        clean = _strip_code(code)
        market = 1 if clean.startswith(("6", "9")) else 0
        client = Finance.factory(market="std")
        df = client.finance(symbol=clean, market=market)
        if df is None or df.empty:
            return {}
        row = df.iloc[-1].to_dict()
        result = {str(k).lower(): v for k, v in row.items()}
        result["_source"] = "mootdx_tcp"
        return result

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
            "fields": "f57,f58,f100,f84,f85,f116,f95",
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
        """
        clean = _strip_code(code)
        type_map = {"balance_sheet": "zcfzb", "income_statement": "lrb", "cashflow": "xjllb"}
        sina_type = type_map.get(report_type, "zcfzb")
        url = "https://quotes.sina.cn/cn/api/json_v2.php/data/CN_STOCK_A_" + sina_type
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

    def get_announcements(self, code: str = "", keyword: str = "",
                           start_date: str = "", end_date: str = "",
                           page: int = 1, page_size: int = 30) -> list[dict]:
        """Fetch A-share announcements via CNInfo (巨潮)."""
        if keyword:
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
                      "url": "https://www.cninfo.com.cn/new/disclosure/detail?announcementId=" + rec.get("announcementId", ""),
                      "publish_date": rec.get("announcementTime", ""),
                      "stock_code": rec.get("secCode", ""),
                      "stock_name": rec.get("secName", "")} for rec in records[:page_size]]
        elif code:
            clean = _strip_code(code)
            org_id = self._cninfo_orgid(clean)
            r = requests.post(
                "https://www.cninfo.com.cn/new/disclosure",
                data={"stock": f"{org_id},{clean}", "pageNum": str(page), "pageSize": str(page_size),
                       "column": "szse", "plate": "sz;sh;bj"},
                headers={"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/"},
                timeout=15,
            )
            data = r.json()
            records = data.get("classifiedAnnouncements", []) if isinstance(data, dict) else []
            return [{"title": rec.get("announcementTitle", ""),
                      "url": "https://www.cninfo.com.cn/new/disclosure/detail?announcementId=" + rec.get("announcementId", ""),
                      "publish_date": rec.get("announcementTime", ""),
                      "stock_code": rec.get("secCode", ""),
                      "stock_name": rec.get("secName", "")} for rec in records[:page_size]]
        return []
