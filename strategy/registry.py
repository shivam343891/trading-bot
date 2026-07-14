"""
Strategy registry — maps name strings to concrete Strategy classes.

Usage:
    from strategy.registry import build_strategies
    pairs = build_strategies(config.ACTIVE_STRATEGIES, config.STRATEGY_PARAMS, config.SYMBOLS)
    # pairs: list of (Strategy, list[str])  — (strategy_instance, symbol_list)
"""
from __future__ import annotations

from strategy.base import Strategy
from strategy.ema_vwap_rsi import EmaVwapRsiStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.orb import OrbStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    EmaVwapRsiStrategy.name: EmaVwapRsiStrategy,
    OrbStrategy.name: OrbStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
}


def build_strategies(
    active: dict[str, list[str] | str] | list[str],
    all_params: dict,
    all_symbols: list[str] | None = None,
) -> list[tuple[Strategy, list[str]]]:
    """
    Instantiate active strategies and return (strategy, symbol_list) pairs.

    active can be:
      - dict  {"strategy_name": ["SYM1", ...] | "all"}   (per-strategy symbol lists)
      - list  ["strategy_name", ...]                       (all strategies on all_symbols)

    all_symbols is required when any strategy uses "all" or when active is a list.
    """
    if isinstance(active, list):
        # Legacy list format: every strategy runs on all_symbols
        active = {name: "all" for name in active}

    if all_symbols is None:
        all_symbols = []

    pairs: list[tuple[Strategy, list[str]]] = []
    for name, sym_spec in active.items():
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy '{name}'. Available: {list(_REGISTRY)}")
        params = all_params.get(name, {})
        strategy = cls(params)

        if sym_spec == "all":
            symbols = list(all_symbols)
        else:
            symbols = list(sym_spec)

        pairs.append((strategy, symbols))

    return pairs
