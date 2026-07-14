"""
Strategy registry — maps name strings to concrete Strategy classes.

Usage:
    from strategy.registry import build_strategies
    strategies = build_strategies(config.ACTIVE_STRATEGIES, config.STRATEGY_PARAMS)
"""
from __future__ import annotations

from strategy.base import Strategy
from strategy.ema_vwap_rsi import EmaVwapRsiStrategy
from strategy.orb import OrbStrategy
from strategy.mean_reversion import MeanReversionStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    EmaVwapRsiStrategy.name: EmaVwapRsiStrategy,
    OrbStrategy.name: OrbStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
}


def build_strategies(active: list[str], all_params: dict) -> list[Strategy]:
    """Instantiate and return all active strategies with their params."""
    strategies: list[Strategy] = []
    for name in active:
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy '{name}'. Available: {list(_REGISTRY)}")
        params = all_params.get(name, {})
        strategies.append(cls(params))
    return strategies
