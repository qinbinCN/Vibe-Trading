"""Shared market data helpers for MCP and local agent tools."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 250

# Symbol -> market type.  When source="auto", each code is grouped by market
# and the best available loader for that market is selected via the fallback
# chain (resolve_loader).  This means A-shares prefer tdx_local when
# TDX_ROOT_PATH is configured, falling back to tencent/mootdx/... seamlessly.
_MARKET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^local:", re.I), "local"),
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "a_share"),
    (re.compile(r"^[A-Z]+\.US$", re.I), "us_equity"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "hk_equity"),
    (re.compile(r"^[A-Z]+-USDT$", re.I), "crypto"),
    (re.compile(r"^[A-Z]+/USDT$", re.I), "crypto"),
]

# Legacy source-level patterns — kept for backward compat with callers that
# pass an explicit source or use detect_source() directly.
_SOURCE_PATTERNS = [
    (re.compile(r"^local:", re.I), "local"),
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "tdx_local"),
    (re.compile(r"^[A-Z]+\.US$", re.I), "yahoo"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "yahoo"),
    (re.compile(r"^[A-Z]+-USDT$", re.I), "okx"),
    (re.compile(r"^[A-Z]+/USDT$", re.I), "ccxt"),
]


def detect_market(code: str) -> str:
    """Return the market type for *code* (e.g. ``a_share``, ``us_equity``)."""
    for pattern, market in _MARKET_PATTERNS:
        if pattern.match(code):
            return market
    return "us_equity"


def detect_source(code: str) -> str:
    """Infer the preferred loader source for a normalized symbol.

    A-shares now return ``tdx_local`` — when TDX_ROOT_PATH is not configured
    the loader-resolver's fallback chain automatically degrades to tencent.
    """
    for pattern, source in _SOURCE_PATTERNS:
        if pattern.match(code):
            return source
    return "tushare"


def get_loader(source: str):
    """Get loader class via registry with fallback support."""
    from backtest.loaders.registry import get_loader_cls_with_fallback

    return get_loader_cls_with_fallback(source)


def _resolve_loader_for_market(market: str):
    """Return the first available loader instance for *market*.

    Walks the fallback chain — A-shares try tdx_local first when configured.
    """
    from backtest.loaders.registry import resolve_loader

    return resolve_loader(market)


def cap_rows(records: list, max_rows: int) -> list | dict[str, object]:
    """Bound a per-symbol row list to keep tool payloads within budget."""
    n = len(records)
    if max_rows < 0:
        max_rows = DEFAULT_MAX_ROWS
    if max_rows == 0 or n <= max_rows:
        return records
    step = math.ceil(n / max_rows)
    sampled = records[::step]
    if sampled[-1] is not records[-1]:
        sampled = sampled + [records[-1]]
    return {
        "rows": n,
        "returned": len(sampled),
        "truncated": True,
        "policy": f"every-{step}th-row (even stride; last bar pinned)",
        "hint": "narrow the date range, coarsen interval, or set max_rows=0 for all rows",
        "data": sampled,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def fetch_market_data(
    *,
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    max_rows: int = DEFAULT_MAX_ROWS,
    loader_resolver: Callable[[str], type] = get_loader,
) -> dict[str, Any]:
    """Fetch normalized OHLCV data through the repository loader layer.

    When *source* is ``"auto"`` (the default), codes are grouped by *market*
    type and each group is served by the first available loader in that
    market's fallback chain.  For A-shares this means ``tdx_local`` when
    ``TDX_ROOT_PATH`` is configured, falling back to tencent → mootdx →
    ... automatically.

    When a loader returns empty or raises, unresolved codes are retried with
    the next loader in the same market's fallback chain.
    """
    results: dict[str, Any] = {}

    if source == "auto":
        # Group by *market* so resolve_loader() picks tdx_local first for A-shares
        groups: dict[str, list[str]] = {}
        for code in codes:
            market = detect_market(code)
            groups.setdefault(market, []).append(code)

        for market, market_codes in groups.items():
            _fetch_with_fallback(
                market, market_codes, start_date, end_date, interval,
                max_rows, results,
            )
    else:
        groups = {source: list(codes)}
        for src, src_codes in groups.items():
            loader_cls = loader_resolver(src)
            loader = loader_cls()
            try:
                data_map = loader.fetch(src_codes, start_date, end_date, interval=interval)
            except Exception:
                logger.exception(
                    "market-data loader %r failed for %s; codes fall through to _unresolved",
                    src, src_codes,
                )
                data_map = {}
            _ingest_data_map(data_map, results, max_rows)

    unresolved = [code for code in codes if code not in results]
    if unresolved:
        results["_unresolved"] = unresolved

    return results


def _fetch_with_fallback(
    market: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    interval: str,
    max_rows: int,
    results: dict[str, Any],
) -> None:
    """Try each loader in *market*'s fallback chain for *codes*.

    Stops when all codes are resolved or the chain is exhausted.
    """
    from backtest.loaders.registry import (
        FALLBACK_CHAINS, LOADER_REGISTRY, _ensure_registered,
    )

    _ensure_registered()
    chain = FALLBACK_CHAINS.get(market, [])
    pending = list(codes)

    for source_name in chain:
        if not pending:
            break
        loader_cls = LOADER_REGISTRY.get(source_name)
        if loader_cls is None:
            continue

        try:
            loader = loader_cls()
        except Exception:
            continue

        if not loader.is_available():
            logger.debug("market-data: %s not available, skipping", source_name)
            continue

        logger.info(
            "market-data: trying %s for %d %s codes: %s",
            loader.name, len(pending), market, pending[:5],
        )

        try:
            data_map = loader.fetch(pending, start_date, end_date, interval=interval)
        except Exception:
            logger.warning(
                "market-data: loader %s failed for %s, trying next in chain",
                loader.name, pending[:5],
            )
            continue

        if data_map:
            before = len(pending)
            _ingest_data_map(data_map, results, max_rows)
            pending = [c for c in codes if c not in results]
            logger.info(
                "market-data: %s resolved %d/%d codes",
                loader.name, before - len(pending), before,
            )

    if pending:
        logger.warning("market-data: exhausted %s chain, %d codes unresolved: %s",
                       market, len(pending), pending[:10])


def _ingest_data_map(
    data_map: dict[str, Any],
    results: dict[str, Any],
    max_rows: int,
) -> None:
    """Convert loader output to JSON-safe rows and merge into *results*."""
    for symbol, df in data_map.items():
        if symbol in results:
            continue
        try:
            records = df.reset_index().to_dict(orient="records")
        except Exception:
            continue
        for row in records:
            for key, value in row.items():
                row[key] = _json_safe(value)
        results[symbol] = cap_rows(records, max_rows)


def fetch_market_data_json(**kwargs: Any) -> str:
    """Fetch market data and return strict JSON."""
    return json.dumps(fetch_market_data(**kwargs), ensure_ascii=False, indent=2, allow_nan=False)
