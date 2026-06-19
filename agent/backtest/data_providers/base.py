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
