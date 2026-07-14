"""Tests for the per-strategy symbol dict in strategy/registry.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from strategy.registry import build_strategies
import config


def test_dict_active_strategies_returns_pairs():
    active = {
        "ema_vwap_rsi": "all",
        "orb": ["NSE_EQ|INE009A01021"],
    }
    pairs = build_strategies(active, config.STRATEGY_PARAMS, config.SYMBOLS)
    assert len(pairs) == 2
    names = [s.name for s, _ in pairs]
    assert "ema_vwap_rsi" in names
    assert "orb" in names


def test_all_expands_to_full_symbol_list():
    active = {"ema_vwap_rsi": "all"}
    pairs = build_strategies(active, config.STRATEGY_PARAMS, config.SYMBOLS)
    _, syms = pairs[0]
    assert syms == config.SYMBOLS


def test_list_spec_uses_provided_symbols():
    subset = ["NSE_EQ|INE009A01021", "NSE_EQ|INE002A01018"]
    active = {"orb": subset}
    pairs = build_strategies(active, config.STRATEGY_PARAMS, config.SYMBOLS)
    _, syms = pairs[0]
    assert syms == subset


def test_legacy_list_input_still_works():
    pairs = build_strategies(["ema_vwap_rsi"], config.STRATEGY_PARAMS, config.SYMBOLS)
    assert len(pairs) == 1
    strat, syms = pairs[0]
    assert strat.name == "ema_vwap_rsi"
    assert syms == config.SYMBOLS


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategies({"does_not_exist": "all"}, {}, config.SYMBOLS)


def test_mean_reversion_symbol_restriction():
    """Mean reversion must only run on high-beta symbols per config."""
    active = {"mean_reversion": config.ACTIVE_STRATEGIES["mean_reversion"]}
    pairs = build_strategies(active, config.STRATEGY_PARAMS, config.SYMBOLS)
    _, syms = pairs[0]
    # Must not include low-beta names like TCS
    tcs_key = "NSE_EQ|INE467B01029"
    assert tcs_key not in syms
