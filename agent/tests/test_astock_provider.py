# agent/tests/test_astock_provider.py
"""Smoke tests for AStockDataProvider — registration, availability, and endpoint signatures."""
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
        assert isinstance(missing, list)

    def test_is_available(self, provider):
        if provider is None:
            pytest.skip("Provider not available (mootdx not installed)")
        result = provider.is_available()
        assert isinstance(result, bool)


class TestEndpointSignatures:
    """Verify all 27 endpoints exist and are callable."""

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


class TestAgentToolDiscovery:
    """Verify all 7 agent tools are auto-discovered."""

    def test_tools_discovered(self):
        from src.tools.__init__ import _discover_subclasses
        tool_names = {t.name for t in _discover_subclasses() if t.name}
        expected = [
            "get_stock_profile", "get_research_reports", "get_stock_news",
            "get_announcements", "get_stock_financials",
            "get_market_signals", "get_capital_flow",
        ]
        missing = [e for e in expected if e not in tool_names]
        assert not missing, f"Missing agent tools: {missing}"


class TestMCPTools:
    """Verify all 7 MCP tool functions exist in mcp_server."""

    def test_mcp_tools_defined(self):
        import mcp_server
        tools = [
            "get_research_reports", "get_stock_news", "get_announcements",
            "get_stock_profile", "get_stock_financials",
            "get_market_signals", "get_capital_flow",
        ]
        missing = []
        for name in tools:
            fn = getattr(mcp_server, name, None)
            if not callable(fn):
                missing.append(name)
        assert not missing, f"Missing MCP tools: {missing}"
