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
