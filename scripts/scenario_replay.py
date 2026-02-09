#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, TextIO, cast

from flow_lens.config import AppConfig, load_app_config
from flow_lens.engine.buffer import (
    PriceSourceMeta,
    PriceSwitchEvent,
    PriorityStickySelector,
    RollingEventBuffer,
)
from flow_lens.engine.constants import (
    Binning,
    Defaults,
    DispScaleConfig,
    EffectivenessDeadband,
    EffectivenessScaling,
    EffortFloor,
    EffortScaleConfig,
    HaloDynamics,
    InputNormalization,
    Persistence,
    SizeScaleConfig,
    Smoothing,
    TimeDomain,
)
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import AggressorSide, Event, SideType

USD_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "USD1")
EPSILON = 1e-9


@dataclass(frozen=True)
class ChunkFile:
    path: Path
    symbol: str
    market: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ReplayEvent:
    event: Event
    actual_symbol: str
    side_type: SideType


class TbtTracker:
    def __init__(self) -> None:
        self._last_ms: dict[str, int] = {}
        self._mean_ms: dict[str, float] = {}
        self._count: dict[str, int] = {}

    def update(self, symbol: str, timestamp_ms: int) -> None:
        last_ms = self._last_ms.get(symbol)
        if last_ms is not None:
            delta = timestamp_ms - last_ms
            if delta > 0:
                count = self._count.get(symbol, 0)
                mean = self._mean_ms.get(symbol, 0.0)
                new_mean = (mean * count + delta) / (count + 1)
                self._mean_ms[symbol] = new_mean
                self._count[symbol] = count + 1
        self._last_ms[symbol] = timestamp_ms

    def min_mean(self, symbols: Iterable[str]) -> float | None:
        minimum: float | None = None
        for symbol in symbols:
            mean = self._mean_ms.get(symbol)
            if mean is None:
                continue
            if minimum is None or mean < minimum:
                minimum = mean
        return minimum


def _parse_time(value: str) -> int:
    value = value.strip()
    if not value:
        raise ValueError("Empty time value.")
    if value.isdigit():
        raw = int(value)
        if raw < 10_000_000_000:
            return raw * 1000
        return raw
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


_CHUNK_RE = re.compile(
    r"^binance_backfill-(?P<symbol>[^-]+)-(?P<market>spot|perp)-"
    r"(?P<start>\d{8}-\d{6})_(?P<end>\d{8}-\d{6})\.jsonl(?:\.gz)?$"
)


def _parse_chunk_filename(path: Path) -> ChunkFile | None:
    match = _CHUNK_RE.match(path.name)
    if not match:
        return None
    symbol = match.group("symbol")
    market = match.group("market")
    start_str = match.group("start")
    end_str = match.group("end")
    try:
        start_ms = int(
            datetime.strptime(start_str, "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
            * 1000
        )
        end_ms = int(
            datetime.strptime(end_str, "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
            * 1000
        )
    except ValueError:
        return None
    return ChunkFile(path=path, symbol=symbol, market=market, start_ms=start_ms, end_ms=end_ms)


def _split_symbol(symbol: str) -> str:
    symbol_upper = symbol.upper()
    for quote in USD_QUOTES:
        if symbol_upper.endswith(quote):
            return symbol_upper[: -len(quote)]
    return symbol_upper


def _normalize_base_symbol(base: str, market: str, strip_1000: bool) -> str:
    if strip_1000 and market == "perp" and base.startswith("1000"):
        return base[4:]
    return base


def _coerce_side_type(value: str, fallback: str) -> SideType:
    candidate = value.lower()
    if candidate in ("spot", "perp"):
        return cast(SideType, candidate)
    fallback_value = fallback.lower()
    if fallback_value in ("spot", "perp"):
        return cast(SideType, fallback_value)
    return "spot"


def _coerce_aggressor_side(value: str) -> AggressorSide:
    candidate = value.lower()
    if candidate in ("buy", "sell"):
        return cast(AggressorSide, candidate)
    return "buy"


def _iter_chunk_events(
    chunk: ChunkFile,
    *,
    start_ms: int,
    end_ms: int,
) -> Iterator[ReplayEvent]:
    opener = gzip.open if chunk.path.suffix == ".gz" else open
    with opener(chunk.path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            ts = int(record["timestamp"])
            if ts < start_ms:
                continue
            if ts >= end_ms:
                break
            side_type = _coerce_side_type(
                str(record.get("side_type", chunk.market)),
                chunk.market,
            )
            aggressor_side = _coerce_aggressor_side(
                str(record.get("aggressor_side", "buy"))
            )
            event = Event(
                timestamp=ts,
                source_id=str(record.get("source_id", "")),
                side_type=side_type,
                aggressor_side=aggressor_side,
                effort_value=float(record.get("effort_value", 0.0)),
                price=float(record.get("price", 0.0)),
            )
            actual_symbol = str(record.get("symbol", chunk.symbol))
            yield ReplayEvent(event=event, actual_symbol=actual_symbol, side_type=side_type)


def _merge_event_iters(iters: list[Iterator[ReplayEvent]]) -> Iterator[ReplayEvent]:
    heap: list[tuple[int, int, ReplayEvent, Iterator[ReplayEvent]]] = []
    for idx, iterator in enumerate(iters):
        try:
            event = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (event.event.timestamp, idx, event, iterator))
    while heap:
        _, idx, event, iterator = heapq.heappop(heap)
        yield event
        try:
            nxt = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (nxt.event.timestamp, idx, nxt, iterator))


def _build_defaults(config: AppConfig) -> Defaults:
    return Defaults(
        time_domain=TimeDomain(update_window_seconds=config.update_window_seconds),
        effort_floor=EffortFloor(
            rolling_window_ticks=config.effort_floor_ticks,
            multiplier_alpha=config.effort_floor_multiplier,
        ),
        dispersion_metric=config.dispersion_metric,
        smoothing=Smoothing(
            dominance_alpha=config.smoothing_dominance_alpha,
            effectiveness_alpha=config.smoothing_effectiveness_alpha,
        ),
        effectiveness_scaling=EffectivenessScaling(tanh_k=config.tanh_k),
        input_normalization=InputNormalization(scale_window_seconds=config.scale_window_seconds),
        persistence=Persistence(
            enabled=config.persist_enabled,
            input_source=config.persist_input,
            input_deadband=config.persist_input_deadband,
            neutral_dir_abs_flash=config.persist_neutral_dir_abs_flash,
            neutral_dir_abs_persist=config.persist_neutral_dir_abs_persist,
            tau_eff_active=config.persist_tau_eff_active,
            tau_dir_active=config.persist_tau_dir_active,
            pivot_active_abs=config.persist_pivot_active_abs,
            pivot_confirm_s=config.persist_pivot_confirm_s,
            pivot_neutralize_tau=config.persist_pivot_neutralize_tau,
            pivot_neutral_zone_abs=config.persist_pivot_neutral_zone_abs,
            rebuild_confirm_s=config.persist_rebuild_confirm_s,
            pivot_cooldown_s=config.persist_pivot_cooldown_s,
            pivot_max_s=config.persist_pivot_max_s,
            max_delta_s_eff_per_second=config.persist_max_delta_s_eff_per_second,
            tau_dir_pivot=config.persist_tau_dir_pivot,
            dormant_quiet_abs=config.persist_dormant_quiet_abs,
            dormant_active_abs=config.persist_dormant_active_abs,
            dormant_quiet_s=config.persist_dormant_quiet_s,
            tau_dormant=config.persist_tau_dormant,
            dormant_effort_norm_threshold=config.persist_dormant_effort_norm_threshold,
        ),
        effectiveness_deadband=EffectivenessDeadband(
            disp_scale_multiplier=config.disp_scale_multiplier
        ),
        disp_scale=DispScaleConfig(
            percentile=config.disp_scale_percentile,
            min_samples=config.disp_scale_min_samples,
            floor_percentile=config.disp_scale_floor_percentile,
        ),
        effort_scale=EffortScaleConfig(
            percentile=config.effort_scale_percentile,
            min_samples=config.effort_scale_min_samples,
        ),
        size_scale=SizeScaleConfig(
            percentile=config.size_scale_percentile,
        ),
        halo_dynamics=HaloDynamics(
            growth_rate=config.halo_growth_rate,
            decay_rate=config.halo_decay_rate,
        ),
        binning=Binning(
            dot_size_thresholds=config.binning_dot_size_thresholds,
            halo_thresholds=config.binning_halo_thresholds,
            hysteresis_band=config.binning_hysteresis_band,
        ),
    )


def _build_price_source_meta(config: AppConfig) -> dict[str, PriceSourceMeta]:
    return {
        source_id: PriceSourceMeta(
            source_id=source_id,
            market_type_for_x=source.market_type_for_x,
            price_eligible=source.price_eligible,
            price_priority=source.price_priority,
        )
        for source_id, source in config.sources.items()
    }


def _open_output(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8")


def _config_snapshot(config: AppConfig) -> dict[str, object]:
    source_registry = {
        source_id: {
            "venue": source.venue,
            "instrument_class": source.instrument_class,
            "market_type_for_x": source.market_type_for_x,
            "price_eligible": source.price_eligible,
            "price_priority": source.price_priority,
            "has_size": source.capabilities.has_size,
            "has_aggressor": source.capabilities.has_aggressor,
            "aggressor_mode": source.capabilities.aggressor_mode,
            "quote_mode": source.capabilities.quote_mode,
        }
        for source_id, source in config.sources.items()
    }
    return {
        "update_window_seconds": config.update_window_seconds,
        "tbt_window_multiplier": config.tbt_window_multiplier,
        "price_selector_policy": config.price_selector_policy,
        "price_selector_stale_failover_ms": config.price_selector_stale_failover_ms,
        "price_selector_recovery_confirm_cycles": config.price_selector_recovery_confirm_cycles,
        "price_selector_switch_cooldown_cycles": config.price_selector_switch_cooldown_cycles,
        "tanh_k": config.tanh_k,
        "scale_window_seconds": config.scale_window_seconds,
        "persist_enabled": config.persist_enabled,
        "persist_input": config.persist_input,
        "persist_input_deadband": config.persist_input_deadband,
        "persist_neutral_dir_abs_flash": config.persist_neutral_dir_abs_flash,
        "persist_neutral_dir_abs_persist": config.persist_neutral_dir_abs_persist,
        "persist_tau_eff_active": config.persist_tau_eff_active,
        "persist_tau_dir_active": config.persist_tau_dir_active,
        "persist_pivot_active_abs": config.persist_pivot_active_abs,
        "persist_pivot_confirm_s": config.persist_pivot_confirm_s,
        "persist_pivot_neutralize_tau": config.persist_pivot_neutralize_tau,
        "persist_pivot_neutral_zone_abs": config.persist_pivot_neutral_zone_abs,
        "persist_rebuild_confirm_s": config.persist_rebuild_confirm_s,
        "persist_pivot_cooldown_s": config.persist_pivot_cooldown_s,
        "persist_pivot_max_s": config.persist_pivot_max_s,
        "persist_max_delta_s_eff_per_second": config.persist_max_delta_s_eff_per_second,
        "persist_tau_dir_pivot": config.persist_tau_dir_pivot,
        "persist_dormant_quiet_abs": config.persist_dormant_quiet_abs,
        "persist_dormant_active_abs": config.persist_dormant_active_abs,
        "persist_dormant_quiet_s": config.persist_dormant_quiet_s,
        "persist_tau_dormant": config.persist_tau_dormant,
        "persist_dormant_effort_norm_threshold": config.persist_dormant_effort_norm_threshold,
        "disp_scale_multiplier": config.disp_scale_multiplier,
        "disp_scale_percentile": config.disp_scale_percentile,
        "disp_scale_min_samples": config.disp_scale_min_samples,
        "disp_scale_floor_percentile": config.disp_scale_floor_percentile,
        "effort_scale_percentile": config.effort_scale_percentile,
        "effort_scale_min_samples": config.effort_scale_min_samples,
        "size_scale_percentile": config.size_scale_percentile,
        "effort_floor_multiplier": config.effort_floor_multiplier,
        "effort_floor_ticks": config.effort_floor_ticks,
        "smoothing_dominance_alpha": config.smoothing_dominance_alpha,
        "smoothing_effectiveness_alpha": config.smoothing_effectiveness_alpha,
        "dispersion_metric": config.dispersion_metric,
        "halo_growth_rate": config.halo_growth_rate,
        "halo_decay_rate": config.halo_decay_rate,
        "binning_dot_size_thresholds": config.binning_dot_size_thresholds,
        "binning_halo_thresholds": config.binning_halo_thresholds,
        "binning_hysteresis_band": config.binning_hysteresis_band,
        "source_registry": source_registry,
    }


def _effective_config_snapshot(
    defaults: Defaults,
    *,
    tbt_window_multiplier: float,
    price_selector_policy: str,
    price_selector_stale_failover_ms: int,
    price_selector_recovery_confirm_cycles: int,
    price_selector_switch_cooldown_cycles: int,
    source_registry: dict[str, object],
) -> dict[str, object]:
    return {
        "update_window_seconds": defaults.time_domain.update_window_seconds,
        "tbt_window_multiplier": tbt_window_multiplier,
        "price_selector_policy": price_selector_policy,
        "price_selector_stale_failover_ms": price_selector_stale_failover_ms,
        "price_selector_recovery_confirm_cycles": price_selector_recovery_confirm_cycles,
        "price_selector_switch_cooldown_cycles": price_selector_switch_cooldown_cycles,
        "tanh_k": defaults.effectiveness_scaling.tanh_k,
        "scale_window_seconds": defaults.input_normalization.scale_window_seconds,
        "persist_enabled": defaults.persistence.enabled,
        "persist_input": defaults.persistence.input_source,
        "persist_input_deadband": defaults.persistence.input_deadband,
        "persist_neutral_dir_abs_flash": defaults.persistence.neutral_dir_abs_flash,
        "persist_neutral_dir_abs_persist": defaults.persistence.neutral_dir_abs_persist,
        "persist_tau_eff_active": defaults.persistence.tau_eff_active,
        "persist_tau_dir_active": defaults.persistence.tau_dir_active,
        "persist_pivot_active_abs": defaults.persistence.pivot_active_abs,
        "persist_pivot_confirm_s": defaults.persistence.pivot_confirm_s,
        "persist_pivot_neutralize_tau": defaults.persistence.pivot_neutralize_tau,
        "persist_pivot_neutral_zone_abs": defaults.persistence.pivot_neutral_zone_abs,
        "persist_rebuild_confirm_s": defaults.persistence.rebuild_confirm_s,
        "persist_pivot_cooldown_s": defaults.persistence.pivot_cooldown_s,
        "persist_pivot_max_s": defaults.persistence.pivot_max_s,
        "persist_max_delta_s_eff_per_second": defaults.persistence.max_delta_s_eff_per_second,
        "persist_tau_dir_pivot": defaults.persistence.tau_dir_pivot,
        "persist_dormant_quiet_abs": defaults.persistence.dormant_quiet_abs,
        "persist_dormant_active_abs": defaults.persistence.dormant_active_abs,
        "persist_dormant_quiet_s": defaults.persistence.dormant_quiet_s,
        "persist_tau_dormant": defaults.persistence.tau_dormant,
        "persist_dormant_effort_norm_threshold": defaults.persistence.dormant_effort_norm_threshold,
        "disp_scale_multiplier": defaults.effectiveness_deadband.disp_scale_multiplier,
        "disp_scale_percentile": defaults.disp_scale.percentile,
        "disp_scale_min_samples": defaults.disp_scale.min_samples,
        "disp_scale_floor_percentile": defaults.disp_scale.floor_percentile,
        "effort_scale_percentile": defaults.effort_scale.percentile,
        "effort_scale_min_samples": defaults.effort_scale.min_samples,
        "size_scale_percentile": defaults.size_scale.percentile,
        "effort_floor_multiplier": defaults.effort_floor.multiplier_alpha,
        "effort_floor_ticks": defaults.effort_floor.rolling_window_ticks,
        "smoothing_dominance_alpha": defaults.smoothing.dominance_alpha,
        "smoothing_effectiveness_alpha": defaults.smoothing.effectiveness_alpha,
        "dispersion_metric": defaults.dispersion_metric,
        "halo_growth_rate": defaults.halo_dynamics.growth_rate,
        "halo_decay_rate": defaults.halo_dynamics.decay_rate,
        "binning_dot_size_thresholds": defaults.binning.dot_size_thresholds,
        "binning_halo_thresholds": defaults.binning.halo_thresholds,
        "binning_hysteresis_band": defaults.binning.hysteresis_band,
        "source_registry": source_registry,
    }


def _value_matches(lhs: object, rhs: object) -> bool:
    if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
        return abs(float(lhs) - float(rhs)) <= 1e-9
    if isinstance(lhs, tuple) and isinstance(rhs, tuple) and len(lhs) == len(rhs):
        return all(_value_matches(lv, rv) for lv, rv in zip(lhs, rhs, strict=False))
    return lhs == rhs


def _verify_replay_config_parity(
    requested: dict[str, object],
    effective: dict[str, object],
) -> None:
    mismatches: list[str] = []
    for key, expected in requested.items():
        if key not in effective:
            mismatches.append(f"{key}: missing in effective config")
            continue
        actual = effective[key]
        if not _value_matches(expected, actual):
            mismatches.append(f"{key}: requested={expected!r} effective={actual!r}")
    if mismatches:
        details = "\n  - " + "\n  - ".join(mismatches)
        raise SystemExit("Replay config parity check failed." + details)


def _write_meta(
    handle: TextIO,
    *,
    config_requested: dict[str, object],
    config_effective: dict[str, object],
    replay: dict[str, object],
) -> None:
    payload = {
        "_meta": {
            "type": "config",
            "config": config_effective,
            "config_requested": config_requested,
            "replay": replay,
        }
    }
    handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    source_registry = config_effective.get("source_registry")
    if not isinstance(source_registry, dict):
        return
    for source_id, details in source_registry.items():
        if not isinstance(source_id, str) or not isinstance(details, dict):
            continue
        record = {
            "event_type": "inference_diagnostics",
            "source_id": source_id,
            "aggressor_mode": details.get("aggressor_mode"),
            "inferred_with_bbo_rate": 0.0,
            "inferred_mid_fallback_rate": 0.0,
            "inferred_tick_rule_fallback_rate": 0.0,
            "unknown_side_rate": 0.0,
            "bbo_age_ms_p50": 0.0,
            "bbo_age_ms_p95": 0.0,
        }
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def _log_record(
    handle: TextIO,
    *,
    symbol: str,
    state: StateSnapshot,
    now_ms: int,
    buffer: RollingEventBuffer,
    tanh_k: float,
    switch_events: tuple[PriceSwitchEvent, ...] = (),
) -> None:
    record = {
        "ts_wall_ms": now_ms,
        "now_ms": now_ms,
        "symbol": symbol,
        "window_ms": buffer.window_delta_ms,
        "window_seconds": state.window_seconds,
        "buffer_event_count": buffer.size,
        "tanh_k": tanh_k,
        "active_price_source_id": state.active_price_source_id,
        "selector_policy": state.selector_policy,
        "price_series_side": state.price_series_side,
        "price_series_used": state.price_series_used,
        "spot_fresh": state.spot_fresh,
        "perp_fresh": state.perp_fresh,
        "last_spot_event_ts": state.last_spot_event_ts,
        "last_perp_event_ts": state.last_perp_event_ts,
        "spot_event_count_window": state.spot_event_count_window,
        "perp_event_count_window": state.perp_event_count_window,
        "price_start": state.price_start,
        "price_end": state.price_end,
        "log_return": state.log_return,
        "delta_price": state.price_end - state.price_start,
        "disp_rate": state.disp_rate,
        "E_rate": state.effort_rate,
        "disp_scale": state.disp_scale,
        "E_scale": state.effort_scale,
        "disp_deadband_active": state.disp_deadband_active,
        "E_spot": state.e_spot,
        "E_perp": state.e_perp,
        "E_dir": state.e_dir,
        "E_dir_sign": 0 if abs(state.e_dir) <= EPSILON else (1 if state.e_dir > 0 else -1),
        "E_total": state.total_effort,
        "D": state.dominance,
        "E_spot_share": state.e_spot_share,
        "X_raw": state.x_raw,
        "X": state.x,
        "size_raw": state.size_raw,
        "size_bin": state.size_bin,
        "size_effort_norm": state.size_effort_norm,
        "size_scale": state.size_scale,
        "disp": state.disp,
        "effort_floor": state.effort_floor,
        "effort_median": state.effort_median,
        "effort_norm": state.effort_norm,
        "gate": state.gate,
        "eff_raw": state.eff_raw,
        "Y_raw": state.y_raw,
        "Y_gated": state.y_gated,
        "Y": state.y,
        "persist_raw": state.persist_raw,
        "persist_slope": state.persist_slope,
        "persist_sign": state.persist_sign,
        "persist_dir_raw": state.persist_dir_raw,
        "persist_dir_sign": state.persist_dir_sign,
        "persist_input": state.persist_input,
        "persist_input_value": state.persist_input_value,
        "persist_a_eff": state.persist_a_eff,
        "persist_a_dir": state.persist_a_dir,
        "halo_raw": state.halo_raw,
        "halo": state.halo,
        "halo_bin": state.halo_bin,
        "source_count_active": state.source_count_active,
        "max_source_share": state.max_source_share,
        "top_source_id": state.top_source_id,
        "top_source_effort": state.top_source_effort,
        "persist_dt_s": state.persist_dt_s,
        "persist_gain_per_second": state.persist_gain_per_second,
        "persist_input_deadband": state.persist_input_deadband,
        "persist_step_coeff": state.persist_step_coeff,
        "persist_alpha_eff": state.persist_alpha_eff,
        "persist_alpha_dir": state.persist_alpha_dir,
        "persist_tau_eff_s": state.persist_tau_eff_s,
        "persist_tau_dir_s": state.persist_tau_dir_s,
        "persist_update_mode": state.persist_update_mode,
        "persist_activity_flag": state.persist_activity_flag,
        "persist_pivot_confirm_elapsed_s": state.persist_pivot_confirm_elapsed_s,
        "persist_pivot_cooldown_remaining_s": state.persist_pivot_cooldown_remaining_s,
        "persist_last_confirmed_dir_sign": state.persist_last_confirmed_dir_sign,
        "persist_pivot_target_dir_sign": state.persist_pivot_target_dir_sign,
        "persist_neutral_dir_abs_flash": state.persist_neutral_dir_abs_flash,
        "persist_neutral_dir_abs_persist": state.persist_neutral_dir_abs_persist,
    }
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    for switch in switch_events:
        switch_record = {
            "event_type": "price_source_switch",
            "ts_wall_ms": now_ms,
            "now_ms": now_ms,
            "symbol": symbol,
            "from_source_id": switch.from_source_id,
            "to_source_id": switch.to_source_id,
            "reason": switch.reason,
            "staleness_from_ms": switch.staleness_from_ms,
            "staleness_to_ms": switch.staleness_to_ms,
            "priority_from": switch.priority_from,
            "priority_to": switch.priority_to,
            "selector_policy": switch.selector_policy,
        }
        handle.write(json.dumps(switch_record, separators=(",", ":")) + "\n")


def _log_switch_only(
    handle: TextIO,
    *,
    symbol: str,
    now_ms: int,
    switch_events: tuple[PriceSwitchEvent, ...],
) -> None:
    for switch in switch_events:
        switch_record = {
            "event_type": "price_source_switch",
            "ts_wall_ms": now_ms,
            "now_ms": now_ms,
            "symbol": symbol,
            "from_source_id": switch.from_source_id,
            "to_source_id": switch.to_source_id,
            "reason": switch.reason,
            "staleness_from_ms": switch.staleness_from_ms,
            "staleness_to_ms": switch.staleness_to_ms,
            "priority_from": switch.priority_from,
            "priority_to": switch.priority_to,
            "selector_policy": switch.selector_policy,
        }
        handle.write(json.dumps(switch_record, separators=(",", ":")) + "\n")


def _log_price_series_unavailable(
    handle: TextIO,
    *,
    symbol: str,
    now_ms: int,
    buffer: RollingEventBuffer,
) -> None:
    record = {
        "event_type": "price_series_unavailable",
        "ts_wall_ms": now_ms,
        "now_ms": now_ms,
        "symbol": symbol,
        "selector_policy": buffer.selector_policy,
        "active_price_source_id": buffer.active_price_source_id,
        "price_series_side": buffer.price_series_side,
        "price_series_used": buffer.price_series_side,
        "reason": "no_eligible_price_source",
    }
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def _iter_chunks(data_dir: Path) -> list[ChunkFile]:
    chunks: list[ChunkFile] = []
    for path in data_dir.rglob("binance_backfill-*.jsonl*"):
        if path.name.endswith(".part"):
            continue
        chunk = _parse_chunk_filename(path)
        if chunk is None:
            continue
        chunks.append(chunk)
    return chunks


def _select_chunks(
    chunks: list[ChunkFile],
    *,
    base_symbol: str,
    strip_1000: bool,
    start_ms: int,
    end_ms: int,
) -> list[ChunkFile]:
    selected: list[ChunkFile] = []
    for chunk in chunks:
        base = _split_symbol(chunk.symbol)
        base = _normalize_base_symbol(base, chunk.market, strip_1000)
        if base != base_symbol:
            continue
        if chunk.end_ms <= start_ms or chunk.start_ms >= end_ms:
            continue
        selected.append(chunk)
    selected.sort(key=lambda c: (c.start_ms, c.market, c.symbol))
    return selected


def _resolve_time_bounds(
    chunks: list[ChunkFile],
    *,
    start_override: int | None,
    end_override: int | None,
) -> tuple[int, int]:
    if not chunks:
        raise SystemExit("No backfill chunks matched the requested symbol.")
    start_ms = min(chunk.start_ms for chunk in chunks)
    end_ms = max(chunk.end_ms for chunk in chunks)
    if start_override is not None:
        start_ms = start_override
    if end_override is not None:
        end_ms = end_override
    if start_ms >= end_ms:
        raise SystemExit("Replay start time must be before end time.")
    return start_ms, end_ms


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay backfill JSONL into diagnostics logs.")
    parser.add_argument(
        "--data-dir",
        default="logs/backfill",
        help="Directory containing binance_backfill JSONL files.",
    )
    parser.add_argument(
        "--scenario-file",
        default="",
        help="Scenario JSON file (from scenario_split). Overrides symbols/start/end.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated base symbols to replay (e.g., BTC,SOL,SHIB).",
    )
    parser.add_argument(
        "--start",
        default="",
        help="Start time (ms since epoch or ISO-8601). Optional.",
    )
    parser.add_argument(
        "--end",
        default="",
        help="End time (ms since epoch or ISO-8601). Optional.",
    )
    parser.add_argument(
        "--window-ms",
        type=int,
        default=None,
        help="Fallback rolling window size in ms (defaults to config update_window_seconds).",
    )
    parser.add_argument(
        "--update-ms",
        type=int,
        default=None,
        help="Update interval in ms (defaults to config update_window_seconds).",
    )
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="Path to app config for scaling parameters.",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/replay",
        help="Output directory for replay diagnostics JSONL.",
    )
    parser.add_argument(
        "--strip-1000",
        action="store_true",
        help="Map perp 1000-prefixed symbols back to base symbol.",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Write output as .jsonl.gz.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f"Missing data dir: {data_dir}")

    scenario_file: Path | None = None
    scenario_payload: dict[str, object] | None = None
    if args.scenario_file:
        scenario_file = Path(args.scenario_file)
        if not scenario_file.exists():
            raise SystemExit(f"Missing scenario file: {scenario_file}")
        with scenario_file.open("r", encoding="utf-8") as handle:
            scenario_payload = json.load(handle)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    chunks = _iter_chunks(data_dir)
    if not chunks:
        raise SystemExit("No backfill files found.")

    config = load_app_config(args.config)
    source_meta = _build_price_source_meta(config)
    defaults = _build_defaults(config)
    config_requested = _config_snapshot(config)
    requested_source_registry = config_requested.get("source_registry")
    if not isinstance(requested_source_registry, dict):
        raise SystemExit("Replay config snapshot missing source_registry.")
    config_effective = _effective_config_snapshot(
        defaults,
        tbt_window_multiplier=config.tbt_window_multiplier,
        price_selector_policy=config.price_selector_policy,
        price_selector_stale_failover_ms=config.price_selector_stale_failover_ms,
        price_selector_recovery_confirm_cycles=config.price_selector_recovery_confirm_cycles,
        price_selector_switch_cooldown_cycles=config.price_selector_switch_cooldown_cycles,
        source_registry=requested_source_registry,
    )
    _verify_replay_config_parity(config_requested, config_effective)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start_override = _parse_time(args.start) if args.start else None
    end_override = _parse_time(args.end) if args.end else None
    update_ms = max(1, args.update_ms or int(config.update_window_seconds * 1000))
    fallback_window_ms = max(1, args.window_ms or int(config.update_window_seconds * 1000))

    if scenario_payload:
        symbol_value = str(scenario_payload.get("symbol", "")).upper()
        if not symbol_value:
            raise SystemExit("Scenario file missing symbol.")
        base_symbols = [symbol_value]
        replay_start_value = scenario_payload.get("replay_start_ms")
        replay_end_value = scenario_payload.get("replay_end_ms")
        if replay_start_value is None or replay_end_value is None:
            raise SystemExit("Scenario file missing replay_start_ms or replay_end_ms.")
        if not isinstance(replay_start_value, (int, float, str)) or not isinstance(
            replay_end_value, (int, float, str)
        ):
            raise SystemExit("Scenario file replay_start_ms/replay_end_ms must be ints.") from None
        try:
            start_override = int(replay_start_value)
            end_override = int(replay_end_value)
        except (TypeError, ValueError):
            raise SystemExit("Scenario file replay_start_ms/replay_end_ms must be ints.") from None
    else:
        if not symbols:
            base_symbols = sorted(
                {
                    _normalize_base_symbol(_split_symbol(chunk.symbol), chunk.market, args.strip_1000)
                    for chunk in chunks
                }
            )
        else:
            base_symbols = symbols

    for base_symbol in base_symbols:
        selected = _select_chunks(
            chunks,
            base_symbol=base_symbol,
            strip_1000=args.strip_1000,
            start_ms=start_override or 0,
            end_ms=end_override or (2**63 - 1),
        )
        if not selected:
            continue

        spot_actuals = sorted({chunk.symbol for chunk in selected if chunk.market == "spot"})
        perp_actuals = sorted({chunk.symbol for chunk in selected if chunk.market == "perp"})

        start_ms, end_ms = _resolve_time_bounds(
            selected, start_override=start_override, end_override=end_override
        )

        iters = [_iter_chunk_events(chunk, start_ms=start_ms, end_ms=end_ms) for chunk in selected]
        merged = _merge_event_iters(iters)

        buffer = RollingEventBuffer(
            window_delta_ms=fallback_window_ms,
            source_meta=source_meta,
            price_selector=PriorityStickySelector(
                stale_failover_ms=config.price_selector_stale_failover_ms,
                recovery_confirm_cycles=config.price_selector_recovery_confirm_cycles,
                switch_cooldown_cycles=config.price_selector_switch_cooldown_cycles,
            ),
        )
        engine = StateEngine(defaults)
        loop = EngineLoop(symbol=base_symbol, buffer=buffer, engine=engine)
        tbt_trackers = {"spot": TbtTracker(), "perp": TbtTracker()}

        timestamp_tag = time.strftime("%Y%m%d-%H%M%S")
        label_suffix = ""
        if scenario_payload:
            label = str(scenario_payload.get("label", "")).strip()
            scenario_id = str(scenario_payload.get("id", "")).strip()
            if label or scenario_id:
                label_suffix = f"-{label or 'scenario'}{('-' + scenario_id) if scenario_id else ''}"
        out_name = f"flow_lens_replay-{base_symbol}{label_suffix}-{timestamp_tag}.jsonl"
        if args.gzip:
            out_name += ".gz"
        out_path = out_dir / out_name

        last_event_ms: int | None = None
        now_ms = start_ms
        try:
            next_event = next(merged)
        except StopIteration:
            continue

        with _open_output(out_path) as handle:
            replay_meta: dict[str, object] = {
                "symbol": base_symbol,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "fallback_window_ms": fallback_window_ms,
                "update_ms": update_ms,
                "config_parity_verified": True,
                "scenario_id": scenario_payload.get("id") if scenario_payload else None,
                "label": scenario_payload.get("label") if scenario_payload else None,
            }
            if scenario_payload:
                replay_meta.update(
                    {
                        "scenario_start_ms": scenario_payload.get("start_ms"),
                        "scenario_end_ms": scenario_payload.get("end_ms"),
                        "pre_roll_ms": scenario_payload.get("pre_roll_ms"),
                    }
                )
            _write_meta(
                handle,
                config_requested=config_requested,
                config_effective=config_effective,
                replay=replay_meta,
            )
            while now_ms < end_ms:
                replay_events: list[ReplayEvent] = []
                while next_event is not None and next_event.event.timestamp <= now_ms:
                    replay_events.append(next_event)
                    last_event_ms = next_event.event.timestamp
                    tracker = tbt_trackers.get(next_event.side_type)
                    if tracker is not None:
                        tracker.update(next_event.actual_symbol, next_event.event.timestamp)
                    try:
                        next_event = next(merged)
                    except StopIteration:
                        next_event = None
                        break

                spot_min = tbt_trackers["spot"].min_mean(spot_actuals) if spot_actuals else None
                perp_min = tbt_trackers["perp"].min_mean(perp_actuals) if perp_actuals else None
                if spot_min is None:
                    tbt_min = perp_min
                elif perp_min is None:
                    tbt_min = spot_min
                else:
                    tbt_min = min(spot_min, perp_min)

                if tbt_min is None:
                    cutoff_ms = fallback_window_ms
                    window_override_ms = fallback_window_ms
                else:
                    cutoff_ms = max(1, int(tbt_min))
                    window_override_ms = max(
                        fallback_window_ms,
                        int(tbt_min * config.tbt_window_multiplier),
                    )

                if replay_events:
                    events = [item.event for item in replay_events]
                    state = loop.step(events, now_ms, window_override_ms=window_override_ms)
                    switch_events = buffer.pop_price_switch_events()
                    did_step = True
                else:
                    if last_event_ms is None or now_ms - last_event_ms > cutoff_ms:
                        state = None
                        switch_events = tuple()
                        did_step = False
                    else:
                        state = loop.step((), now_ms, window_override_ms=window_override_ms)
                        switch_events = buffer.pop_price_switch_events()
                        did_step = True

                if state is not None:
                    _log_record(
                        handle,
                        symbol=base_symbol,
                        state=state,
                        now_ms=now_ms,
                        buffer=buffer,
                        tanh_k=defaults.effectiveness_scaling.tanh_k,
                        switch_events=switch_events,
                    )
                elif switch_events:
                    _log_switch_only(
                        handle,
                        symbol=base_symbol,
                        now_ms=now_ms,
                        switch_events=switch_events,
                    )
                if did_step and state is None and buffer.active_price_source_id is None:
                    _log_price_series_unavailable(
                        handle,
                        symbol=base_symbol,
                        now_ms=now_ms,
                        buffer=buffer,
                    )
                now_ms += update_ms

        print(f"Wrote replay diagnostics: {out_path}")


if __name__ == "__main__":
    main()
