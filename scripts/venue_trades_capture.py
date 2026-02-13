#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gzip
import http.client
import json
import random
import ssl
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets
from hyperliquid.info import Info


@dataclass(frozen=True)
class Candidate:
    candidate_id: str  # e.g. "binance_spot", "okx_perp"
    venue: str  # e.g. "binance"
    market_type: str  # "spot" | "perp"
    base_symbol: str  # e.g. "BTC"


@dataclass(frozen=True)
class TradePrint:
    candidate_id: str
    venue: str
    market_type: str
    base_symbol: str
    ts_exchange_ms: int
    ts_venue_ms: int | None
    ts_recv_ms: int
    price: float
    size: float  # base qty when available; else 0
    notional: float  # price * size when available; else 0


class _DedupeState:
    def __init__(self, *, max_size: int) -> None:
        self._max_size = max_size
        self._q: deque[tuple[int, str, str]] = deque()
        self._s: set[tuple[int, str, str]] = set()

    def should_emit(self, sig: tuple[int, str, str]) -> bool:
        if sig in self._s:
            return False
        self._s.add(sig)
        self._q.append(sig)
        while len(self._q) > self._max_size:
            old = self._q.popleft()
            self._s.discard(old)
        return True


def _tradeprint_from_hyperliquid_row(
    candidate: Candidate,
    row: dict[str, Any],
    *,
    recv_ms: int,
    hyperliquid_ts_mode: str,
    dedupe: _DedupeState,
) -> TradePrint | None:
    base_symbol = candidate.base_symbol
    if row.get("coin") != base_symbol:
        return None
    px_raw = row.get("px")
    sz_raw = row.get("sz")
    price = _as_float(px_raw)
    qty = _as_float(sz_raw)
    if price is None:
        return None
    try:
        ts_venue_ms = int(row.get("time") or 0)
    except (TypeError, ValueError):
        ts_venue_ms = 0
    if ts_venue_ms <= 0:
        return None

    sig = (
        ts_venue_ms,
        str(row.get("hash") or row.get("tid") or row.get("tradeId") or ""),
        f"{px_raw}:{sz_raw}:{row.get('side') or row.get('dir') or ''}",
    )
    if not dedupe.should_emit(sig):
        return None

    if hyperliquid_ts_mode == "venue":
        ts_exchange_ms = ts_venue_ms
    elif hyperliquid_ts_mode == "recv":
        ts_exchange_ms = recv_ms
    else:
        raise ValueError(f"Invalid hyperliquid_ts_mode: {hyperliquid_ts_mode!r}")

    q = float(qty) if qty is not None else 0.0
    notional = float(price) * q if q > 0 else 0.0
    return TradePrint(
        candidate_id=candidate.candidate_id,
        venue=candidate.venue,
        market_type=candidate.market_type,
        base_symbol=base_symbol,
        ts_exchange_ms=ts_exchange_ms,
        ts_venue_ms=ts_venue_ms,
        ts_recv_ms=recv_ms,
        price=float(price),
        size=q,
        notional=notional,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _raw_text(raw: object) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        data = raw
        if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
            try:
                data = gzip.decompress(data)
            except OSError:
                return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _parse_symbol_list(value: str) -> list[str]:
    out: list[str] = []
    for part in value.split(","):
        sym = part.strip().upper()
        if sym:
            out.append(sym)
    return out


def _default_candidate_ids() -> list[str]:
    return [
        "binance_spot",
        "binance_perp",
        "coinbase_spot",
        "okx_spot",
        "okx_perp",
        "bybit_spot",
        "bybit_perp",
        "gate_spot",
        "gate_perp",
        "kucoin_spot",
        "kucoin_perp",
        "deribit_perp",
        "hyperliquid_perp",
        "upbit_spot",
    ]


def _candidate_id_list(value: str) -> list[str]:
    if not value.strip():
        return _default_candidate_ids()
    return [part.strip() for part in value.split(",") if part.strip()]


def _candidate_specs(candidate_id: str, *, base_symbols: list[str]) -> list[Candidate]:
    venue, _, market_type = candidate_id.partition("_")
    if not venue or not market_type:
        raise ValueError(f"Invalid candidate id: {candidate_id!r}")
    if market_type not in {"spot", "perp"}:
        raise ValueError(f"Invalid market type in candidate id: {candidate_id!r}")
    return [
        Candidate(
            candidate_id=candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
        )
        for base_symbol in base_symbols
    ]


def _binance_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _bybit_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _okx_inst_id(*, market_type: str, base_symbol: str) -> str:
    if market_type == "spot":
        return f"{base_symbol}-USDT"
    return f"{base_symbol}-USDT-SWAP"


def _coinbase_product_id(*, base_symbol: str) -> str:
    return f"{base_symbol}-USD"


def _gate_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}_USDT"


def _kucoin_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}-USDT"


def _kucoin_perp_symbol(*, base_symbol: str) -> str:
    if base_symbol == "BTC":
        return "XBTUSDTM"
    return f"{base_symbol}USDTM"


def _upbit_market_code(*, base_symbol: str) -> str:
    return f"KRW-{base_symbol}"


def _binance_ws_url(*, market_type: str, stream: str) -> str:
    if market_type == "spot":
        return f"wss://stream.binance.com:9443/ws/{stream}"
    return f"wss://fstream.binance.com/ws/{stream}"


def _okx_ws_url() -> str:
    return "wss://ws.okx.com:8443/ws/v5/public"


def _bybit_ws_url(*, market_type: str) -> str:
    if market_type == "spot":
        return "wss://stream.bybit.com/v5/public/spot"
    return "wss://stream.bybit.com/v5/public/linear"


def _coinbase_ws_url() -> str:
    return "wss://ws-feed.exchange.coinbase.com"


def _gate_ws_url(*, market_type: str) -> str:
    if market_type == "spot":
        return "wss://api.gateio.ws/ws/v4/"
    return "wss://fx-ws.gateio.ws/v4/ws/usdt"


def _deribit_ws_url() -> str:
    return "wss://www.deribit.com/ws/api/v2"


def _hyperliquid_ws_url() -> str:
    return "wss://api.hyperliquid.xyz/ws"


def _upbit_ws_url() -> str:
    return "wss://api.upbit.com/websocket/v1"


_KUCOIN_LOCK = asyncio.Lock()
_KUCOIN_CACHED: dict[str, tuple[int, str, float]] = {}  # market_type -> (fetched_ms, ws_url, ping_interval_s)


def _kucoin_fetch_bullet_sync(*, host: str) -> tuple[str, float]:
    context = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, timeout=5, context=context)
    conn.request("POST", "/api/v1/bullet-public", body=b"", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("KuCoin bullet response: expected dict")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("KuCoin bullet response: missing data")
    token = data.get("token")
    servers = data.get("instanceServers")
    if not isinstance(token, str) or not isinstance(servers, list) or not servers:
        raise ValueError("KuCoin bullet response: missing token/servers")
    server0 = servers[0]
    if not isinstance(server0, dict):
        raise ValueError("KuCoin bullet response: invalid server")
    endpoint = server0.get("endpoint")
    ping_interval_ms = server0.get("pingInterval")
    if not isinstance(endpoint, str) or not isinstance(ping_interval_ms, (int, float)):
        raise ValueError("KuCoin bullet response: missing endpoint/pingInterval")
    connect_id = f"trades-{random.randrange(1_000_000_000)}"
    ws_url = f"{endpoint}?token={token}&connectId={connect_id}"
    return ws_url, float(ping_interval_ms) / 1000.0


async def _kucoin_ws_info(*, market_type: str) -> tuple[str, float]:
    async with _KUCOIN_LOCK:
        now_ms = _now_ms()
        cached = _KUCOIN_CACHED.get(market_type)
        if cached is not None:
            fetched_ms, ws_url, ping_interval_s = cached
            if now_ms - fetched_ms < 9 * 60 * 1000:
                return ws_url, ping_interval_s

        host = "api.kucoin.com" if market_type == "spot" else "api-futures.kucoin.com"
        ws_url, ping_interval_s = await asyncio.to_thread(_kucoin_fetch_bullet_sync, host=host)
        _KUCOIN_CACHED[market_type] = (now_ms, ws_url, ping_interval_s)
        return ws_url, ping_interval_s


async def _send_periodic(
    ws: Any,
    *,
    payload: object,
    every_s: float,
    stop_at_ms: int,
) -> None:
    while _now_ms() < stop_at_ms:
        await asyncio.sleep(max(0.5, every_s))
        if _now_ms() >= stop_at_ms:
            return
        if isinstance(payload, str):
            await ws.send(payload)
        else:
            await ws.send(json.dumps(payload, separators=(",", ":")))


def _trade_from_payload(
    candidate: Candidate,
    payload: dict[str, Any],
    *,
    recv_ms: int,
    hyperliquid_ts_mode: str,
    hyperliquid_dedupe: _DedupeState | None,
) -> list[TradePrint]:
    venue = candidate.venue
    market_type = candidate.market_type
    base_symbol = candidate.base_symbol

    out: list[TradePrint] = []

    if venue == "binance":
        # aggTrade: {"T": <ms>, "p":"...", "q":"..."}
        try:
            ts_venue_ms = int(payload.get("T") or payload.get("E") or 0)
            price = float(payload["p"])
            qty = float(payload["q"])
        except (KeyError, TypeError, ValueError):
            return []
        if ts_venue_ms <= 0:
            ts_venue_ms = None
        ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
        notional = price * qty if qty > 0 else 0.0
        out.append(
            TradePrint(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_venue_ms=ts_venue_ms,
                ts_recv_ms=recv_ms,
                price=price,
                size=qty,
                notional=notional,
            )
        )
        return out

    if venue == "okx":
        # trades: {"data":[{"ts":"..","px":"..","sz":".."}]}
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                ts_venue_ms = int(row.get("ts") or 0)
                price = float(row["px"])
                qty = float(row.get("sz") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            if ts_venue_ms <= 0:
                ts_venue_ms = None
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
            notional = price * qty if qty > 0 else 0.0
            out.append(
                TradePrint(
                    candidate_id=candidate.candidate_id,
                    venue=venue,
                    market_type=market_type,
                    base_symbol=base_symbol,
                    ts_exchange_ms=ts_exchange_ms,
                    ts_venue_ms=ts_venue_ms,
                    ts_recv_ms=recv_ms,
                    price=price,
                    size=qty,
                    notional=notional,
                )
            )
        return out

    if venue == "bybit":
        # publicTrade: {"topic":"publicTrade.BTCUSDT","ts":<ms>,"data":[{"T":<ms>,"p":"..","v":".."}]}
        topic = payload.get("topic")
        if not isinstance(topic, str) or not topic.startswith("publicTrade."):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        for row in data:
            if not isinstance(row, dict):
                continue
            price = _as_float(row.get("p") or row.get("price"))
            qty = _as_float(row.get("v") or row.get("size") or row.get("q"))
            if price is None:
                continue
            try:
                ts_venue_ms = int(row.get("T") or payload.get("ts") or 0)
            except (TypeError, ValueError):
                ts_venue_ms = 0
            if ts_venue_ms <= 0:
                ts_venue_ms = None
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
            q = float(qty) if qty is not None else 0.0
            notional = float(price) * q if q > 0 else 0.0
            out.append(
                TradePrint(
                    candidate_id=candidate.candidate_id,
                    venue=venue,
                    market_type=market_type,
                    base_symbol=base_symbol,
                    ts_exchange_ms=ts_exchange_ms,
                    ts_venue_ms=ts_venue_ms,
                    ts_recv_ms=recv_ms,
                    price=float(price),
                    size=q,
                    notional=notional,
                )
            )
        return out

    if venue == "coinbase":
        # match: {"type":"match","time":"...Z","price":"..","size":".."}
        if payload.get("type") != "match":
            return []
        price = _as_float(payload.get("price"))
        qty = _as_float(payload.get("size"))
        if price is None:
            return []
        ts_venue_ms: int | None = None
        time_str = payload.get("time")
        if isinstance(time_str, str) and time_str.endswith("Z"):
            from datetime import datetime

            try:
                ts_venue_ms = int(datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                ts_venue_ms = None
        ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
        q = float(qty) if qty is not None else 0.0
        notional = float(price) * q if q > 0 else 0.0
        out.append(
            TradePrint(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_venue_ms=ts_venue_ms,
                ts_recv_ms=recv_ms,
                price=float(price),
                size=q,
                notional=notional,
            )
        )
        return out

    if venue == "gate":
        # Best-effort: spot.trades and futures.trades updates include array result rows.
        result = payload.get("result")
        rows: list[dict[str, Any]]
        if isinstance(result, list):
            rows = [row for row in result if isinstance(row, dict)]
        elif isinstance(result, dict):
            # Gate spot commonly uses a single dict result (not a list).
            rows = [result]
        else:
            return []
        # Spot rows often: {"id":..., "create_time_ms":..., "price":"..", "amount":".."}
        # Futures rows often: {"id":..., "create_time_ms":..., "price":"..", "size":..}
        for row in rows:
            price = _as_float(row.get("price"))
            qty = _as_float(row.get("amount") or row.get("size"))
            if price is None:
                continue
            raw_ts = _as_float(
                row.get("create_time_ms")
                or row.get("time_ms")
                or row.get("t")
                or row.get("create_time")
                or row.get("time")
                or payload.get("time_ms")
                or payload.get("t")
                or payload.get("time")
                or 0
            )
            if raw_ts is None or raw_ts <= 0:
                ts_venue_ms = None
            else:
                # Gate sometimes provides seconds in create_time; normalize to ms if needed.
                ts_venue_ms = int(raw_ts * 1000) if raw_ts < 10_000_000_000 else int(raw_ts)
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
            q = float(qty) if qty is not None else 0.0
            notional = float(price) * q if q > 0 else 0.0
            out.append(
                TradePrint(
                    candidate_id=candidate.candidate_id,
                    venue=venue,
                    market_type=market_type,
                    base_symbol=base_symbol,
                    ts_exchange_ms=ts_exchange_ms,
                    ts_venue_ms=ts_venue_ms,
                    ts_recv_ms=recv_ms,
                    price=float(price),
                    size=q,
                    notional=notional,
                )
            )
        return out

    if venue == "kucoin":
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        if market_type == "spot":
            # /market/match: {"data":{"time":"<ns>","price":"..","size":".."}}
            price = _as_float(data.get("price"))
            qty = _as_float(data.get("size"))
            if price is None:
                return []
            try:
                ts_ns = int(data.get("time") or 0)
            except (TypeError, ValueError):
                ts_ns = 0
            ts_venue_ms = (ts_ns // 1_000_000) if ts_ns > 0 else None
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
        else:
            # /contractMarket/execution: {"data":{"ts":<ns>,"price":"..","size":".."}}
            price = _as_float(data.get("price"))
            qty = _as_float(data.get("size"))
            if price is None:
                return []
            try:
                ts_ns = int(data.get("ts") or 0)
            except (TypeError, ValueError):
                ts_ns = 0
            ts_venue_ms = (ts_ns // 1_000_000) if ts_ns > 0 else None
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms

        q = float(qty) if qty is not None else 0.0
        notional = float(price) * q if q > 0 else 0.0
        out.append(
            TradePrint(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_venue_ms=ts_venue_ms,
                ts_recv_ms=recv_ms,
                price=float(price),
                size=q,
                notional=notional,
            )
        )
        return out

    if venue == "deribit":
        # {"method":"subscription","params":{"data":[{"timestamp":<ms>,"price":..,"amount":..}, ...]}}
        if payload.get("method") != "subscription":
            return []
        params = payload.get("params")
        if not isinstance(params, dict):
            return []
        data = params.get("data")
        if not isinstance(data, list):
            return []
        for row in data:
            if not isinstance(row, dict):
                continue
            price = _as_float(row.get("price"))
            qty = _as_float(row.get("amount"))
            if price is None:
                continue
            try:
                ts_venue_ms = int(row.get("timestamp") or 0)
            except (TypeError, ValueError):
                ts_venue_ms = 0
            if ts_venue_ms <= 0:
                ts_venue_ms = None
            ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
            q = float(qty) if qty is not None else 0.0
            notional = float(price) * q if q > 0 else 0.0
            out.append(
                TradePrint(
                    candidate_id=candidate.candidate_id,
                    venue=venue,
                    market_type=market_type,
                    base_symbol=base_symbol,
                    ts_exchange_ms=ts_exchange_ms,
                    ts_venue_ms=ts_venue_ms,
                    ts_recv_ms=recv_ms,
                    price=float(price),
                    size=q,
                    notional=notional,
                )
            )
        return out

    if venue == "hyperliquid":
        if payload.get("channel") != "trades":
            return []
        if hyperliquid_dedupe is None:
            raise ValueError("hyperliquid_dedupe must be set for hyperliquid venue")
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        for row in data:
            if not isinstance(row, dict):
                continue
            tp = _tradeprint_from_hyperliquid_row(
                candidate,
                row,
                recv_ms=recv_ms,
                hyperliquid_ts_mode=hyperliquid_ts_mode,
                dedupe=hyperliquid_dedupe,
            )
            if tp is None:
                continue
            out.append(tp)
        return out

    if venue == "upbit":
        # Upbit is KRW-quoted; included for completeness but usually excluded from USD≈USDT studies.
        if payload.get("type") != "trade":
            return []
        code = payload.get("code")
        if not isinstance(code, str) or not code.endswith(f"-{base_symbol}"):
            return []
        price = _as_float(payload.get("trade_price"))
        qty = _as_float(payload.get("trade_volume"))
        if price is None:
            return []
        try:
            ts_venue_ms = int(payload.get("trade_timestamp") or payload.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts_venue_ms = 0
        if ts_venue_ms <= 0:
            ts_venue_ms = None
        ts_exchange_ms = ts_venue_ms if ts_venue_ms is not None else recv_ms
        q = float(qty) if qty is not None else 0.0
        notional = float(price) * q if q > 0 else 0.0
        out.append(
            TradePrint(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_venue_ms=ts_venue_ms,
                ts_recv_ms=recv_ms,
                price=float(price),
                size=q,
                notional=notional,
            )
        )
        return out

    return []


async def _ws_run(
    candidate: Candidate,
    *,
    out_queue: asyncio.Queue[TradePrint],
    stop_at_ms: int,
    hyperliquid_ts_mode: str,
    hyperliquid_debug_path: Path | None,
    hyperliquid_debug_lock: asyncio.Lock | None,
    gate_debug_path: Path | None,
    gate_debug_lock: asyncio.Lock | None,
) -> None:
    venue = candidate.venue
    market_type = candidate.market_type
    base_symbol = candidate.base_symbol

    subscribe_payload: object | None
    periodic_payload: object | None = None
    periodic_every_s = 0.0
    hyperliquid_dedupe: _DedupeState | None = None

    if venue == "binance":
        stream = f"{_binance_symbol(base_symbol=base_symbol).lower()}@aggTrade"
        url = _binance_ws_url(market_type=market_type, stream=stream)
        subscribe_payload = None
    elif venue == "okx":
        url = _okx_ws_url()
        inst = _okx_inst_id(market_type=market_type, base_symbol=base_symbol)
        subscribe_payload = {"op": "subscribe", "args": [{"channel": "trades", "instId": inst}]}
    elif venue == "bybit":
        url = _bybit_ws_url(market_type=market_type)
        sym = _bybit_symbol(base_symbol=base_symbol)
        subscribe_payload = {"op": "subscribe", "args": [f"publicTrade.{sym}"]}
    elif venue == "coinbase":
        url = _coinbase_ws_url()
        product = _coinbase_product_id(base_symbol=base_symbol)
        subscribe_payload = {"type": "subscribe", "product_ids": [product], "channels": ["matches"]}
    elif venue == "gate":
        url = _gate_ws_url(market_type=market_type)
        sym = _gate_symbol(base_symbol=base_symbol)
        channel = "spot.trades" if market_type == "spot" else "futures.trades"
        subscribe_payload = {"time": int(time.time()), "channel": channel, "event": "subscribe", "payload": [sym]}
    elif venue == "kucoin":
        url, ping_interval_s = await _kucoin_ws_info(market_type=market_type)
        if market_type == "spot":
            sym = _kucoin_symbol(base_symbol=base_symbol)
            topic = f"/market/match:{sym}"
        else:
            sym = _kucoin_perp_symbol(base_symbol=base_symbol)
            topic = f"/contractMarket/execution:{sym}"
        subscribe_payload = {"id": str(random.randrange(1_000_000_000)), "type": "subscribe", "topic": topic, "response": True}
        periodic_payload = {"id": str(random.randrange(1_000_000_000)), "type": "ping"}
        periodic_every_s = max(5.0, 0.8 * ping_interval_s)
    elif venue == "deribit":
        url = _deribit_ws_url()
        # 100ms is public and sufficient for lead/lag at 0-4s horizons.
        channel = f"trades.{base_symbol}-PERPETUAL.100ms"
        subscribe_payload = {"jsonrpc": "2.0", "id": 1, "method": "public/subscribe", "params": {"channels": [channel]}}
    elif venue == "hyperliquid":
        url = _hyperliquid_ws_url()
        subscribe_payload = {"method": "subscribe", "subscription": {"type": "trades", "coin": base_symbol}}
        hyperliquid_dedupe = _DedupeState(max_size=200_000)
    elif venue == "upbit":
        url = _upbit_ws_url()
        code = _upbit_market_code(base_symbol=base_symbol)
        subscribe_payload = [{"ticket": f"trades-{candidate.candidate_id}-{base_symbol}"}, {"type": "trade", "codes": [code]}]
    else:
        raise ValueError(f"Unsupported venue: {venue}")

    backoff_s = 1.0
    conn_id = 0
    while _now_ms() < stop_at_ms:
        try:
            conn_id += 1
            ws = await asyncio.wait_for(
                websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=5),
                timeout=8,
            )
            periodic_task: asyncio.Task[None] | None = None
            try:
                if subscribe_payload is not None:
                    await ws.send(json.dumps(subscribe_payload, separators=(",", ":")))
                if periodic_payload is not None and periodic_every_s > 0:
                    periodic_task = asyncio.create_task(
                        _send_periodic(ws, payload=periodic_payload, every_s=periodic_every_s, stop_at_ms=stop_at_ms)
                    )

                last_hl_recv_ms: int | None = None
                if venue == "hyperliquid" and hyperliquid_debug_path is not None and hyperliquid_debug_lock is not None:
                    async with hyperliquid_debug_lock:
                        with hyperliquid_debug_path.open("a", encoding="utf-8") as f:
                            f.write(
                                json.dumps(
                                    {
                                        "_event": "conn_open",
                                        "conn_id": conn_id,
                                        "candidate_id": candidate.candidate_id,
                                        "base_symbol": base_symbol,
                                        "ts_recv_ms": _now_ms(),
                                    },
                                    separators=(",", ":"),
                                )
                                + "\n"
                            )

                while _now_ms() < stop_at_ms:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    recv_ms = _now_ms()
                    text = _raw_text(raw)
                    if text is None:
                        continue
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue

                    if venue == "gate" and gate_debug_path is not None and gate_debug_lock is not None:
                        async with gate_debug_lock:
                            with gate_debug_path.open("a", encoding="utf-8") as f:
                                f.write(
                                    json.dumps(
                                        {
                                            "_event": "gate_msg",
                                            "conn_id": conn_id,
                                            "candidate_id": candidate.candidate_id,
                                            "market_type": market_type,
                                            "base_symbol": base_symbol,
                                            "ts_recv_ms": recv_ms,
                                            "payload": payload,
                                        },
                                        separators=(",", ":"),
                                    )
                                    + "\n"
                                )

                    events: list[dict[str, Any]] = []
                    if isinstance(payload, dict):
                        events = [payload]
                    elif isinstance(payload, list):
                        for item in payload:
                            if isinstance(item, dict):
                                events.append(item)
                    else:
                        continue

                    for event in events:
                        # Optional Hyperliquid message-level debugging:
                        # trade feeds can be batchy, include recent-history snapshots, or resend prints.
                        # We log per-message min/max venue timestamp for the coin, plus emitted count.
                        hl_rows_for_coin = 0
                        hl_min_venue_ms: int | None = None
                        hl_max_venue_ms: int | None = None
                        if (
                            venue == "hyperliquid"
                            and hyperliquid_debug_path is not None
                            and hyperliquid_debug_lock is not None
                            and event.get("channel") == "trades"
                        ):
                            data = event.get("data")
                            if isinstance(data, list):
                                for row in data:
                                    if not isinstance(row, dict):
                                        continue
                                    if row.get("coin") != base_symbol:
                                        continue
                                    hl_rows_for_coin += 1
                                    try:
                                        t = int(row.get("time") or 0)
                                    except (TypeError, ValueError):
                                        continue
                                    if t <= 0:
                                        continue
                                    if hl_min_venue_ms is None or t < hl_min_venue_ms:
                                        hl_min_venue_ms = t
                                    if hl_max_venue_ms is None or t > hl_max_venue_ms:
                                        hl_max_venue_ms = t

                        trades = _trade_from_payload(
                            candidate,
                            event,
                            recv_ms=recv_ms,
                            hyperliquid_ts_mode=hyperliquid_ts_mode,
                            hyperliquid_dedupe=hyperliquid_dedupe,
                        )
                        if venue == "hyperliquid" and hyperliquid_debug_path is not None and hyperliquid_debug_lock is not None:
                            gap_ms = (recv_ms - last_hl_recv_ms) if last_hl_recv_ms is not None else None
                            last_hl_recv_ms = recv_ms
                            async with hyperliquid_debug_lock:
                                with hyperliquid_debug_path.open("a", encoding="utf-8") as f:
                                    f.write(
                                        json.dumps(
                                            {
                                                "_event": "hl_msg",
                                                "conn_id": conn_id,
                                                "candidate_id": candidate.candidate_id,
                                                "base_symbol": base_symbol,
                                                "ts_recv_ms": recv_ms,
                                                "rows_for_coin": hl_rows_for_coin,
                                                "min_ts_venue_ms": hl_min_venue_ms,
                                                "max_ts_venue_ms": hl_max_venue_ms,
                                                "recv_minus_max_venue_ms": (recv_ms - hl_max_venue_ms)
                                                if hl_max_venue_ms is not None
                                                else None,
                                                "recv_gap_ms": gap_ms,
                                                "emitted_trades": len(trades),
                                            },
                                            separators=(",", ":"),
                                        )
                                        + "\n"
                                    )

                        for trade in trades:
                            await out_queue.put(trade)
            finally:
                if periodic_task is not None:
                    periodic_task.cancel()
                    await asyncio.gather(periodic_task, return_exceptions=True)
                await ws.close()
            backoff_s = 1.0
        except (asyncio.TimeoutError, OSError, websockets.WebSocketException):
            remaining_s = max(0.0, (stop_at_ms - _now_ms()) / 1000.0)
            if remaining_s <= 0:
                return
            await asyncio.sleep(min(backoff_s, remaining_s))
            backoff_s = min(10.0, backoff_s * 1.7)


async def _hyperliquid_sdk_run(
    candidate: Candidate,
    *,
    out_queue: asyncio.Queue[TradePrint],
    stop_at_ms: int,
    hyperliquid_ts_mode: str,
    hyperliquid_debug_path: Path | None,
    hyperliquid_debug_lock: asyncio.Lock | None,
) -> None:
    loop = asyncio.get_running_loop()
    dedupe = _DedupeState(max_size=200_000)
    conn_id = 1

    def on_msg(ws_msg: Any) -> None:
        recv_ms = _now_ms()
        if not isinstance(ws_msg, dict) or ws_msg.get("channel") != "trades":
            return
        data = ws_msg.get("data")
        if not isinstance(data, list):
            return

        rows_for_coin = 0
        min_ts_venue_ms: int | None = None
        max_ts_venue_ms: int | None = None
        emitted = 0

        for row in data:
            if not isinstance(row, dict):
                continue
            if row.get("coin") != candidate.base_symbol:
                continue
            rows_for_coin += 1
            try:
                t = int(row.get("time") or 0)
            except (TypeError, ValueError):
                t = 0
            if t > 0:
                if min_ts_venue_ms is None or t < min_ts_venue_ms:
                    min_ts_venue_ms = t
                if max_ts_venue_ms is None or t > max_ts_venue_ms:
                    max_ts_venue_ms = t

            tp = _tradeprint_from_hyperliquid_row(
                candidate,
                row,
                recv_ms=recv_ms,
                hyperliquid_ts_mode=hyperliquid_ts_mode,
                dedupe=dedupe,
            )
            if tp is None:
                continue
            emitted += 1
            try:
                loop.call_soon_threadsafe(out_queue.put_nowait, tp)
            except RuntimeError:
                # Event loop closed.
                return

        if hyperliquid_debug_path is not None and hyperliquid_debug_lock is not None:
            # This callback runs on a thread owned by the SDK websocket manager.
            payload = {
                "_event": "hl_sdk_msg",
                "conn_id": conn_id,
                "candidate_id": candidate.candidate_id,
                "base_symbol": candidate.base_symbol,
                "ts_recv_ms": recv_ms,
                "rows_for_coin": rows_for_coin,
                "min_ts_venue_ms": min_ts_venue_ms,
                "max_ts_venue_ms": max_ts_venue_ms,
                "recv_minus_max_venue_ms": (recv_ms - max_ts_venue_ms) if max_ts_venue_ms is not None else None,
                "emitted_trades": emitted,
            }

            async def _write() -> None:
                # Re-check inside coroutine for type-narrowing.
                if hyperliquid_debug_path is None or hyperliquid_debug_lock is None:
                    return
                async with hyperliquid_debug_lock:
                    with hyperliquid_debug_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(payload, separators=(",", ":")) + "\n")

            try:
                asyncio.run_coroutine_threadsafe(_write(), loop)
            except RuntimeError:
                return

    info = Info()
    sub_id = info.subscribe({"type": "trades", "coin": candidate.base_symbol}, on_msg)
    try:
        while _now_ms() < stop_at_ms:
            await asyncio.sleep(0.25)
    finally:
        try:
            info.unsubscribe({"type": "trades", "coin": candidate.base_symbol}, sub_id)
        finally:
            info.disconnect_websocket()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Capture multi-venue trade prints into a shared schema for tournament scoring.")
    parser.add_argument("--symbols", default="BTC,SOL", help="Comma-separated base symbols to capture (default: BTC,SOL).")
    parser.add_argument("--candidates", default="", help="Comma-separated candidate ids (default: broad list).")
    parser.add_argument("--duration-s", type=int, default=1800, help="Capture duration in seconds (default: 1800).")
    parser.add_argument("--out-root", default="logs/venue_trade_runs", help="Directory root for capture outputs (default: logs/venue_trade_runs).")
    parser.add_argument("--gzip", action="store_true", help="Gzip the raw jsonl capture log.")
    parser.add_argument(
        "--hyperliquid-ts-mode",
        choices=["recv", "venue"],
        default="venue",
        help=(
            "How to populate ts_exchange_ms for hyperliquid_perp. "
            "'venue' (default) uses Hyperliquid's trade 'time' field; "
            "'recv' uses local receive time for tournament alignment. "
            "In both modes, ts_venue_ms records the raw venue timestamp when present."
        ),
    )
    parser.add_argument(
        "--debug-hyperliquid",
        action="store_true",
        help="Write Hyperliquid message-level debug jsonl into the run_dir (default: disabled).",
    )
    parser.add_argument(
        "--hyperliquid-transport",
        choices=["sdk", "raw"],
        default="sdk",
        help="How to connect for hyperliquid_perp (default: sdk).",
    )
    parser.add_argument(
        "--debug-gate",
        action="store_true",
        help="Write Gate raw message-level debug jsonl into the run_dir (default: disabled).",
    )
    args = parser.parse_args()

    base_symbols = _parse_symbol_list(args.symbols)
    if not base_symbols:
        raise SystemExit("No symbols provided.")

    candidate_ids = _candidate_id_list(args.candidates)
    candidates: list[Candidate] = []
    for cid in candidate_ids:
        candidates.extend(_candidate_specs(cid, base_symbols=base_symbols))

    start_ms = _now_ms()
    stop_at_ms = start_ms + int(args.duration_s * 1000)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_root) / f"{run_id}_trades_capture"
    _ensure_dir(out_dir)

    raw_path = out_dir / "trades.jsonl"
    if args.gzip:
        raw_path = raw_path.with_suffix(".jsonl.gz")

    q: asyncio.Queue[TradePrint] = asyncio.Queue(maxsize=200_000)
    hl_mode = str(args.hyperliquid_ts_mode)
    hl_transport = str(args.hyperliquid_transport)
    hl_debug_path: Path | None = None
    hl_debug_lock: asyncio.Lock | None = None
    if bool(args.debug_hyperliquid):
        hl_debug_path = out_dir / "hyperliquid_debug.jsonl"
        hl_debug_path.write_text(
            json.dumps(
                {
                    "_meta": {
                        "type": "hyperliquid_debug",
                        "created_at_ms": start_ms,
                        "symbols": base_symbols,
                        "hyperliquid_ts_mode": hl_mode,
                    }
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        hl_debug_lock = asyncio.Lock()

    gate_debug_path: Path | None = None
    gate_debug_lock: asyncio.Lock | None = None
    if bool(args.debug_gate):
        gate_debug_path = out_dir / "gate_debug.jsonl"
        gate_debug_path.write_text(
            json.dumps(
                {
                    "_meta": {
                        "type": "gate_debug",
                        "created_at_ms": start_ms,
                        "symbols": base_symbols,
                        "candidates": candidate_ids,
                    }
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        gate_debug_lock = asyncio.Lock()
    tasks = [
        asyncio.create_task(
            _hyperliquid_sdk_run(
                c,
                out_queue=q,
                stop_at_ms=stop_at_ms,
                hyperliquid_ts_mode=hl_mode,
                hyperliquid_debug_path=hl_debug_path,
                hyperliquid_debug_lock=hl_debug_lock,
            )
        )
        if c.venue == "hyperliquid" and str(args.hyperliquid_transport) == "sdk"
        else asyncio.create_task(
            _ws_run(
                c,
                out_queue=q,
                stop_at_ms=stop_at_ms,
                hyperliquid_ts_mode=hl_mode,
                hyperliquid_debug_path=hl_debug_path,
                hyperliquid_debug_lock=hl_debug_lock,
                gate_debug_path=gate_debug_path,
                gate_debug_lock=gate_debug_lock,
            )
        )
        for c in candidates
    ]

    opener = gzip.open if args.gzip else open
    with opener(raw_path, "wt", encoding="utf-8") as handle:  # type: ignore[call-overload]
        meta = {
            "_meta": {
                "type": "venue_trade_capture",
                "created_at_ms": start_ms,
                "stop_at_ms": stop_at_ms,
                "symbols": base_symbols,
                "candidates": candidate_ids,
                "hyperliquid_ts_mode": hl_mode,
                "hyperliquid_transport": hl_transport,
            }
        }
        handle.write(json.dumps(meta, separators=(",", ":")) + "\n")
        handle.flush()

        while _now_ms() < stop_at_ms or not q.empty():
            try:
                trade = await asyncio.wait_for(q.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            handle.write(json.dumps(trade.__dict__, separators=(",", ":")) + "\n")

    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3.0)
    except asyncio.TimeoutError:
        # Some network stack operations can be slow/uninterruptible; don't hang the capture on shutdown.
        pass

    print(f"run_dir: {out_dir}")
    print(f"raw: {raw_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
