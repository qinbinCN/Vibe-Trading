"""TDX local loader: A-share OHLCV from local TongDaXin (通达信) .day files.

Reads daily K-line data directly from the TDX installation directory — no network
calls, no IP bans, no authentication. Also computes turnover_rate (换手率) locally
by joining with the latest gpcw financial-data zip for float-share counts.

Prerequisites:
- ``pytdx`` installed (pip install pytdx)
- ``TDX_ROOT_PATH`` env var pointing to the TDX installation root (e.g. ``D:/new_tdx``)
- TDX daily data downloaded via the 通达信 client (at minimum ``vipdoc/{sh,sz,bj}/lday/``)
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TDX file layout helpers
# ---------------------------------------------------------------------------


def _tdx_root() -> Optional[Path]:
    """Return the configured TDX root directory, or None."""
    env = os.environ.get("TDX_ROOT_PATH", "").strip()
    if not env:
        return None
    p = Path(env)
    return p if p.is_dir() else None


def _market_dir(code_upper: str) -> Optional[str]:
    """Return the vipdoc market subdirectory for a symbol suffix.

    >>> _market_dir("600000.SH")
    'sh'
    >>> _market_dir("000001.SZ")
    'sz'
    """
    if code_upper.endswith(".SH"):
        return "sh"
    if code_upper.endswith(".SZ"):
        return "sz"
    if code_upper.endswith(".BJ"):
        return "bj"
    return None


def _code_to_day_path(root: Path, code: str) -> Optional[Path]:
    """Map a symbol like '600000.SH' to the .day file path.

    Returns None when the market cannot be determined.
    """
    market = _market_dir(code.upper())
    if market is None:
        return None
    bare = code.split(".")[0]
    return root / "vipdoc" / market / "lday" / f"{market}{bare}.day"


def _get_float_shares(code: str, gpcw_cache: dict) -> Optional[float]:
    """Look up a usable share-count denominator for turnover-rate from gpcw.

    Tries fields in descending order of accuracy:
      1. col268 — 自由流通股 (free-float, most accurate for turnover)
      2. col240 — 已上市流通A股 (listed tradable A-shares)
      3. col239 — 总股本 (total share capital, last resort)

    Returns None when the gpcw cache is empty or the code is not found.
    """
    if not gpcw_cache:
        return None
    df = gpcw_cache.get("_df")
    if df is None or df.empty:
        return None
    bare = code.split(".")[0]
    if bare not in df.index:
        return None

    for col in ("col268", "col240", "col239"):
        try:
            val = df.loc[bare, col]
            if hasattr(val, "iloc"):
                val = float(val.iloc[0]) if len(val) > 0 else None
            fv = float(val) if val else 0.0
            if fv > 0:
                return fv
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return None


# ---------------------------------------------------------------------------
# gpcw cache — loaded once per loader instance, refreshed lazily
# ---------------------------------------------------------------------------

_gpcw_cache: dict = {}


def _load_gpcw_cache() -> dict:
    """Load the latest valid gpcw zip from the local TDX directory.

    The result is cached at module level so the big zip is read at most once
    per process lifetime.  Returns a dict with ``_df`` key holding the
    full DataFrame, plus per-code lookup helpers.
    """
    if _gpcw_cache:
        return _gpcw_cache

    root = _tdx_root()
    if root is None:
        return {}

    cw_dir = root / "vipdoc" / "cw"
    if not cw_dir.is_dir():
        return {}

    try:
        from pytdx.reader import HistoryFinancialReader
    except ImportError:
        logger.debug("tdx_local: pytdx not installed, cannot load gpcw cache")
        return {}

    zips = sorted(cw_dir.glob("gpcw*.zip"), reverse=True)
    # pick the newest non-empty zip (> 5 KB to skip placeholder files)
    for z in zips:
        if z.stat().st_size > 5000:
            break
    else:
        return {}

    try:
        reader = HistoryFinancialReader()
        df = reader.get_df(str(z))
        if df is not None and not df.empty:
            _gpcw_cache["_df"] = df
            _gpcw_cache["_report_date"] = df["report_date"].iloc[0] if "report_date" in df.columns else None
            # pre-compute float share lookup (try col268→col240→col239)
            for float_col in ("col268", "col240", "col239"):
                if float_col in df.columns:
                    float_series = df[float_col]
                    _gpcw_cache["_float"] = {}
                    for idx, val in float_series.items():
                        try:
                            fv = float(val)
                            if fv > 0:
                                _gpcw_cache["_float"][str(idx)] = fv
                        except (ValueError, TypeError):
                            continue
                    break
            logger.info(
                "tdx_local: loaded gpcw %s — %d stocks",
                z.name, df.shape[0],
            )
    except Exception as exc:
        logger.warning("tdx_local: failed to load gpcw cache: %s", exc)

    return _gpcw_cache


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


@register
class DataLoader:
    """TongDaXin local-file A-share OHLCV loader.

    Reads .day binary files directly from the TDX installation directory.
    Computes turnover_rate (换手率) locally using the latest gpcw financial
    data for float-share counts — no network calls at all.
    """

    name = "tdx_local"
    markets = {"a_share"}
    requires_auth = False

    def __init__(self) -> None:
        pass

    def is_available(self) -> bool:
        """Return True when pytdx is installed and TDX_ROOT_PATH points to a valid directory."""
        try:
            import pytdx  # noqa: F401
        except ImportError:
            return False
        root = _tdx_root()
        if root is None:
            return False
        # At least one lday directory must exist with .day files
        for mkt in ("sh", "sz", "bj"):
            lday = root / "vipdoc" / mkt / "lday"
            if lday.is_dir() and any(lday.glob("*.day")):
                return True
        return False

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch A-share OHLCV from local TDX .day files.

        Args:
            codes: Symbol list with ``.SH/.SZ/.BJ`` suffix.
            start_date: YYYY-MM-DD.
            end_date: YYYY-MM-DD.
            interval: Only ``1D`` is supported (daily bars).
            fields: Ignored (all OHLCV columns returned).

        Returns:
            Mapping symbol -> OHLCV DataFrame with optional ``turnover_rate`` column.
        """
        validate_date_range(start_date, end_date)
        if interval != "1D":
            raise ValueError(
                f"tdx_local only supports interval='1D', got {interval!r}"
            )

        root = _tdx_root()
        if root is None:
            logger.warning("tdx_local: TDX_ROOT_PATH is not set or not a directory")
            return {}

        logger.info(
            "tdx_local: fetching %d codes [%s → %s] from %s",
            len(codes), start_date, end_date, root,
        )

        # Ensure gpcw cache is warm for turnover rate
        gpcw = _load_gpcw_cache()

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            if not code.upper().endswith((".SH", ".SZ", ".BJ")):
                logger.debug("tdx_local: skipping non-A-share symbol %s", code)
                continue

            day_path = _code_to_day_path(root, code)
            if day_path is None or not day_path.exists():
                logger.debug("tdx_local: .day file not found for %s", code)
                continue

            try:
                df = cached_loader_fetch(
                    source=self.name,
                    symbol=code,
                    timeframe=interval,
                    start_date=start_date,
                    end_date=end_date,
                    fields=None,
                    fetch=lambda c=code: self._fetch_one(
                        c, str(day_path), start_date, end_date, gpcw,
                    ),
                )
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("tdx_local failed for %s: %s", code, exc)

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_one(
        code: str,
        day_path: str,
        start_date: str,
        end_date: str,
        gpcw: dict,
    ) -> Optional[pd.DataFrame]:
        """Read and normalise a single .day file."""
        import io
        from contextlib import redirect_stdout

        from pytdx.reader import TdxDailyBarReader

        reader = TdxDailyBarReader()
        df = None
        # Suppress pytdx "Unknown security type !" print for codes it doesn't
        # recognise (e.g. 科创板 688xxx).  The manual parser handles those.
        with redirect_stdout(io.StringIO()):
            try:
                df = reader.get_df_by_file(day_path)
            except Exception:
                try:
                    df = reader.get_df(day_path)
                except Exception:
                    pass

        if df is None or df.empty:
            # pytdx cannot handle some codes (e.g. 科创板 688xxx).
            # Fall back to manual parsing of the 32-byte record format.
            df = DataLoader._parse_day_raw(day_path)

        if df is None or df.empty:
            return None

        return DataLoader._normalize(df, start_date, end_date, code, gpcw)

    @staticmethod
    def _parse_day_raw(path: str) -> pd.DataFrame:
        """Manually parse a TDX .day file (32 bytes per record).

        Format (little-endian)::

            uint32  date    (YYYYMMDD packed decimal)
            uint32  open    (price × 100)
            uint32  high    (price × 100)
            uint32  low     (price × 100)
            uint32  close   (price × 100)
            float32 amount  (traded value in yuan)
            uint32  volume  (shares traded in 手 / 100-share lots)
            uint32  reserved
        """
        import struct

        record_size = 32
        records: list[dict] = []
        with open(path, "rb") as f:
            while True:
                chunk = f.read(record_size)
                if len(chunk) < record_size:
                    break
                date_raw, open_raw, high_raw, low_raw, close_raw = struct.unpack_from(
                    "<IIIII", chunk, 0
                )
                amount_raw, vol_raw, _reserved = struct.unpack_from(
                    "<fII", chunk, 20
                )
                # Decode TDX date: YYYYMMDD packed as integer
                yr = date_raw // 10000
                md = date_raw % 10000
                mo = md // 100
                dy = md % 100
                try:
                    dt = pd.Timestamp(year=yr, month=mo, day=dy)
                except (ValueError, OverflowError):
                    continue

                records.append({
                    "date": dt,
                    "open": open_raw / 100.0,
                    "high": high_raw / 100.0,
                    "low": low_raw / 100.0,
                    "close": close_raw / 100.0,
                    "amount": amount_raw,
                    # vol_raw is shares (股); divide by 100 → 手 (lots),
                    # matching pytdx's convention so the turnover-rate formula
                    # (volume * 100 / float_shares * 100) works uniformly.
                    "volume": vol_raw / 100.0,
                })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = df.set_index("date")
        df.index.name = "date"
        df = df.sort_index()
        return df

    @staticmethod
    def _normalize(
        df: pd.DataFrame,
        start_date: str,
        end_date: str,
        code: str,
        gpcw: dict,
    ) -> Optional[pd.DataFrame]:
        """Convert pytdx output to the standard OHLCV contract.

        pytdx ``TdxDailyBarReader`` returns::

            index:  date (datetime64)
            cols:   open, high, low, close, amount, volume

        We clip to the requested date range, add turnover_rate when float-share
        data is available, and drop auxiliary columns.
        """
        out = df.copy()

        # Standardise index
        if out.index.name != "trade_date":
            if out.index.name == "date" or out.index.name is None:
                out.index.name = "trade_date"

        out = out.sort_index()

        # Clip
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        out = out.loc[start_ts:end_ts]

        if out.empty:
            return None

        # Core OHLCV columns
        for col in ("open", "high", "low", "close", "volume"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        ohlcv_cols = ["open", "high", "low", "close", "volume"]
        out = out[ohlcv_cols].dropna(subset=["open", "high", "low", "close"])

        if out.empty:
            return None

        # --- turnover_rate (本地换手率) ---
        # TDX .day volume is in 手 (100-share lots)；float_shares is in 股.
        # 换手率(%) = 成交股数 / 自由流通股 × 100
        #           = volume × 100 / float_shares × 100
        float_shares = _get_float_shares(code, gpcw)
        if float_shares and float_shares > 0:
            out["turnover_rate"] = (
                out["volume"] * 100.0 / float_shares * 100.0
            )
        else:
            out["turnover_rate"] = float("nan")

        return out
