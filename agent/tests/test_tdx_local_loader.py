"""Tests for tdx_local_loader — TDX local-file A-share OHLCV loader."""

from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

# Ensure the backtest package is importable
# (test runner should cd into agent/ or set PYTHONPATH)


# ---------------------------------------------------------------------------
# Fake .day file builder (32-byte records, little-endian)
# ---------------------------------------------------------------------------

def _make_day_record(date_int, open_p, high_p, low_p, close_p, amount, volume):
    """Build one 32-byte .day record.

    *date_int* is YYYYMMDD as an integer (e.g. 20260618).
    Prices are in yuan; *volume* is shares (will be stored ×100 for the raw format).
    """
    return struct.pack(
        "<IIIII f I I",
        date_int,
        int(open_p * 100),
        int(high_p * 100),
        int(low_p * 100),
        int(close_p * 100),
        float(amount),
        int(volume),     # shares, raw format (manual parser divides by 100)
        0,               # reserved
    )


# ---------------------------------------------------------------------------
# Tests: module-level helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    """Test the symbol→path mapping and configuration helpers."""

    def test_market_dir_sh(self):
        from backtest.loaders.tdx_local_loader import _market_dir
        assert _market_dir("600000.SH") == "sh"

    def test_market_dir_sz(self):
        from backtest.loaders.tdx_local_loader import _market_dir
        assert _market_dir("000001.SZ") == "sz"

    def test_market_dir_bj(self):
        from backtest.loaders.tdx_local_loader import _market_dir
        assert _market_dir("430047.BJ") == "bj"

    def test_market_dir_unknown(self):
        from backtest.loaders.tdx_local_loader import _market_dir
        assert _market_dir("AAPL.US") is None

    def test_code_to_day_path(self, tmp_path):
        from backtest.loaders.tdx_local_loader import _code_to_day_path
        root = tmp_path
        # Create the directory structure
        (root / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        path = _code_to_day_path(root, "600000.SH")
        assert path == root / "vipdoc" / "sh" / "lday" / "sh600000.day"

    def test_code_to_day_path_unknown_market(self, tmp_path):
        from backtest.loaders.tdx_local_loader import _code_to_day_path
        assert _code_to_day_path(tmp_path, "AAPL.US") is None

    def test_tdx_root_not_set(self, monkeypatch):
        monkeypatch.delenv("TDX_ROOT_PATH", raising=False)
        from backtest.loaders.tdx_local_loader import _tdx_root
        assert _tdx_root() is None

    def test_tdx_root_invalid(self, monkeypatch):
        monkeypatch.setenv("TDX_ROOT_PATH", "/nonexistent/path/12345")
        from backtest.loaders.tdx_local_loader import _tdx_root
        assert _tdx_root() is None


# ---------------------------------------------------------------------------
# Tests: manual .day file parser
# ---------------------------------------------------------------------------

class TestParseDayRaw:
    """Test the fallback manual parser."""

    def test_basic_parse(self, tmp_path):
        from backtest.loaders.tdx_local_loader import DataLoader

        day_path = tmp_path / "sh600000.day"
        records = [
            _make_day_record(20260616, 9.50, 9.56, 9.42, 9.46, 5.156e8, 54422960),
            _make_day_record(20260617, 9.48, 9.55, 9.22, 9.24, 7.198e8, 77190400),
            _make_day_record(20260618, 9.20, 9.25, 9.07, 9.09, 7.633e8, 83656384),
        ]
        day_path.write_bytes(b"".join(records))

        df = DataLoader._parse_day_raw(str(day_path))
        assert len(df) == 3
        assert list(df.columns) == ["open", "high", "low", "close", "amount", "volume"]
        # Volume should be in 手 (divided by 100)
        assert df.iloc[-1]["volume"] == pytest.approx(836563.84)
        assert df.iloc[-1]["close"] == 9.09
        assert df.index.name == "date"

    def test_empty_file(self, tmp_path):
        from backtest.loaders.tdx_local_loader import DataLoader
        day_path = tmp_path / "empty.day"
        day_path.write_bytes(b"")
        df = DataLoader._parse_day_raw(str(day_path))
        assert df.empty

    def test_partial_record(self, tmp_path):
        from backtest.loaders.tdx_local_loader import DataLoader
        day_path = tmp_path / "partial.day"
        day_path.write_bytes(b"\x00" * 20)  # less than 32 bytes
        df = DataLoader._parse_day_raw(str(day_path))
        assert df.empty


# ---------------------------------------------------------------------------
# Tests: turn-over rate
# ---------------------------------------------------------------------------

class TestFloatShares:
    """Test the float-share lookup logic."""

    def test_col268_preferred(self):
        import pandas as pd
        from backtest.loaders.tdx_local_loader import _get_float_shares

        df = pd.DataFrame(
            {"col268": [1e9], "col240": [5e8], "col239": [2e9]},
            index=["600519"],
        )
        gpcw = {"_df": df}
        result = _get_float_shares("600519.SH", gpcw)
        assert result == 1e9

    def test_fallback_to_col240(self):
        import pandas as pd
        from backtest.loaders.tdx_local_loader import _get_float_shares

        df = pd.DataFrame(
            {"col268": [0.0], "col240": [5e8], "col239": [2e9]},
            index=["688981"],
        )
        gpcw = {"_df": df}
        result = _get_float_shares("688981.SH", gpcw)
        assert result == 5e8

    def test_fallback_to_col239(self):
        import pandas as pd
        from backtest.loaders.tdx_local_loader import _get_float_shares

        df = pd.DataFrame(
            {"col268": [0.0], "col240": [0.0], "col239": [2e9]},
            index=["688981"],
        )
        gpcw = {"_df": df}
        result = _get_float_shares("688981.SH", gpcw)
        assert result == 2e9

    def test_code_not_found(self):
        import pandas as pd
        from backtest.loaders.tdx_local_loader import _get_float_shares

        df = pd.DataFrame(
            {"col268": [1e9]}, index=["600519"],
        )
        gpcw = {"_df": df}
        assert _get_float_shares("000001.SZ", gpcw) is None

    def test_empty_cache(self):
        from backtest.loaders.tdx_local_loader import _get_float_shares
        assert _get_float_shares("600519.SH", {}) is None


# ---------------------------------------------------------------------------
# Tests: DataLoader class
# ---------------------------------------------------------------------------

class TestDataLoader:
    """Test the registered DataLoader class."""

    def test_is_available_pytdx_missing(self, monkeypatch):
        monkeypatch.delenv("TDX_ROOT_PATH", raising=False)
        # Simulate pytdx not installed
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pytdx":
                raise ImportError("no pytdx")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            from backtest.loaders.tdx_local_loader import DataLoader
            loader = DataLoader()
            assert loader.is_available() is False

    def test_is_available_no_tdx_root(self, monkeypatch):
        monkeypatch.delenv("TDX_ROOT_PATH", raising=False)
        from backtest.loaders.tdx_local_loader import DataLoader
        loader = DataLoader()
        # pytdx is installed, but TDX_ROOT_PATH not set → unavailable
        assert loader.is_available() is False

    def test_fetch_unsupported_interval(self):
        from backtest.loaders.tdx_local_loader import DataLoader
        loader = DataLoader()
        with pytest.raises(ValueError, match="1D"):
            loader.fetch(["600519.SH"], "2026-01-01", "2026-06-18", interval="1H")

    def test_fetch_non_ashare_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TDX_ROOT_PATH", str(tmp_path))
        (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        from backtest.loaders.tdx_local_loader import DataLoader
        loader = DataLoader()
        result = loader.fetch(["AAPL.US"], "2026-01-01", "2026-06-18")
        assert result == {}

    def test_registry_attributes(self):
        from backtest.loaders.tdx_local_loader import DataLoader
        assert DataLoader.name == "tdx_local"
        assert DataLoader.markets == {"a_share"}
        assert DataLoader.requires_auth is False


# ---------------------------------------------------------------------------
# Integration: registry
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    """Verify tdx_local is properly registered."""

    def test_in_valid_sources(self):
        from backtest.loaders.registry import VALID_SOURCES
        assert "tdx_local" in VALID_SOURCES

    def test_in_loader_registry(self):
        from backtest.loaders.registry import _ensure_registered, LOADER_REGISTRY
        _ensure_registered()
        assert "tdx_local" in LOADER_REGISTRY

    def test_in_fallback_chain(self):
        from backtest.loaders.registry import FALLBACK_CHAINS
        assert "tdx_local" in FALLBACK_CHAINS["a_share"]

    def test_no_network_fallback(self):
        from backtest.loaders.registry import _NO_NETWORK_FALLBACK_SOURCES
        assert "tdx_local" in _NO_NETWORK_FALLBACK_SOURCES
