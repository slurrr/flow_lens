from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AdapterConfig:
    type: str
    symbols: list[str]


@dataclass(frozen=True)
class AppConfig:
    adapters: Mapping[str, AdapterConfig]
    tbt_window_multiplier: float
    tanh_k: float
    scale_window_seconds: float


def load_app_config(path: Path | str = Path("config/app.toml")) -> AppConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    adapters_section = data.get("adapters", {})
    if not isinstance(adapters_section, dict) or not adapters_section:
        raise ValueError("app.toml must define adapters.")
    runtime_section = data.get("runtime", {})
    if not isinstance(runtime_section, dict):
        raise ValueError("runtime config must be a table.")
    tbt_window_multiplier = runtime_section.get("tbt_window_multiplier", 4.0)
    tanh_k = runtime_section.get("tanh_k", 500.0)
    scale_window_seconds = runtime_section.get("scale_window_seconds", 600.0)
    if not isinstance(tbt_window_multiplier, (int, float)):
        raise ValueError("runtime.tbt_window_multiplier must be a number.")
    if not isinstance(tanh_k, (int, float)):
        raise ValueError("runtime.tanh_k must be a number.")
    if not isinstance(scale_window_seconds, (int, float)):
        raise ValueError("runtime.scale_window_seconds must be a number.")

    adapters: dict[str, AdapterConfig] = {}
    for name, adapter in adapters_section.items():
        if not isinstance(adapter, dict):
            raise ValueError(f"Adapter {name} config must be a table.")
        adapter_type = adapter.get("type")
        symbols = adapter.get("symbols")
        if not isinstance(adapter_type, str):
            raise ValueError(f"Adapter {name} is missing a type.")
        if not isinstance(symbols, list) or not symbols:
            raise ValueError(f"Adapter {name} is missing symbols.")

        normalized_symbols = [normalize_symbol(str(s)) for s in symbols]
        adapters[name] = AdapterConfig(
            type=adapter_type,
            symbols=normalized_symbols,
        )

    return AppConfig(
        adapters=adapters,
        tbt_window_multiplier=float(tbt_window_multiplier),
        tanh_k=float(tanh_k),
        scale_window_seconds=float(scale_window_seconds),
    )


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("-", "").replace("_", "").upper()
