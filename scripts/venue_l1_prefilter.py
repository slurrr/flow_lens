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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets


@dataclass(frozen=True)
class Candidate:
    candidate_id: str  # e.g. "binance_spot", "okx_perp"
    venue: str  # e.g. "binance"
    market_type: str  # "spot" | "perp"
    base_symbol: str  # e.g. "BTC"


@dataclass(frozen=True)
class BboUpdate:
    candidate_id: str
    venue: str
    market_type: str
    base_symbol: str
    ts_exchange_ms: int
    ts_recv_ms: int
    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 1:
        return sorted_values[-1]
    idx = int(round(pct * (len(sorted_values) - 1)))
    return sorted_values[idx]


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
        # Some venues may compress frames (gzip). Treat it opportunistically; if it fails, fall back to UTF-8 decode.
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


class Reservoir:
    def __init__(self, capacity: int, *, seed: int) -> None:
        self._cap = capacity
        self._rng = random.Random(seed)
        self._n_seen = 0
        self._values: list[float] = []

    def add(self, value: float) -> None:
        self._n_seen += 1
        if len(self._values) < self._cap:
            self._values.append(value)
            return
        j = self._rng.randrange(0, self._n_seen)
        if j < self._cap:
            self._values[j] = value

    def summary(self) -> dict[str, float]:
        values = sorted(self._values)
        return {
            "n_seen": float(self._n_seen),
            "n_sample": float(len(values)),
            "p50": _pct(values, 0.50),
            "p95": _pct(values, 0.95),
            "p99": _pct(values, 0.99),
            "max": (values[-1] if values else 0.0),
        }


@dataclass
class CandidateStats:
    candidate_id: str
    venue: str
    market_type: str
    base_symbol: str
    start_ms: int
    end_ms: int
    update_count: int
    stale_gaps_gt_1s: int
    stale_gaps_gt_2s: int
    max_gap_s: float
    spread_bps: Reservoir
    jitter_ms: Reservoir

    last_recv_ms: int | None = None

    def add(self, update: BboUpdate) -> None:
        self.end_ms = max(self.end_ms, update.ts_recv_ms)
        self.update_count += 1

        if self.last_recv_ms is not None:
            gap_s = max(0.0, (update.ts_recv_ms - self.last_recv_ms) / 1000.0)
            self.max_gap_s = max(self.max_gap_s, gap_s)
            if gap_s > 1.0:
                self.stale_gaps_gt_1s += 1
            if gap_s > 2.0:
                self.stale_gaps_gt_2s += 1
        self.last_recv_ms = update.ts_recv_ms

        mid = 0.5 * (update.bid_px + update.ask_px)
        if mid > 0 and update.ask_px >= update.bid_px:
            spread_bps = ((update.ask_px - update.bid_px) / mid) * 1e4
            self.spread_bps.add(spread_bps)

        # Some venues’ exchange timestamps are not tightly aligned to local wall time (clock skew),
        # so treat jitter as a magnitude-only “timebase mismatch + delivery latency” measure.
        jitter = float(abs(update.ts_recv_ms - update.ts_exchange_ms))
        self.jitter_ms.add(jitter)

    def finalize(self) -> dict[str, object]:
        duration_s = max(0.001, (self.end_ms - self.start_ms) / 1000.0)
        updates_per_s = self.update_count / duration_s
        # Staleness is measured as a per-update gap condition. This is not “time in stale state”, but works
        # well as a quick comparative quality signal.
        stale_rate_1s = self.stale_gaps_gt_1s / max(1, self.update_count - 1)
        stale_rate_2s = self.stale_gaps_gt_2s / max(1, self.update_count - 1)
        return {
            "candidate_id": self.candidate_id,
            "venue": self.venue,
            "market_type": self.market_type,
            "base_symbol": self.base_symbol,
            "duration_s": duration_s,
            "updates": self.update_count,
            "updates_per_s": updates_per_s,
            "spread_bps": self.spread_bps.summary(),
            "jitter_ms": self.jitter_ms.summary(),
            "stale_gap_rate_gt_1s": stale_rate_1s,
            "stale_gap_rate_gt_2s": stale_rate_2s,
            "max_gap_s": self.max_gap_s,
        }


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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
        "gate_spot",
        "gate_perp",
        "bitget_spot",
        "bitget_perp",
        "kucoin_spot",
        "kucoin_perp",
        "mexc_spot",
        "mexc_perp",
        "okx_spot",
        "okx_perp",
        "bybit_spot",
        "bybit_perp",
        "deribit_perp",
        "hyperliquid_perp",
        "upbit_spot",
        "xt_perp",
    ]


def _candidate_id_list(value: str) -> list[str]:
    if not value.strip():
        return _default_candidate_ids()
    return [part.strip() for part in value.split(",") if part.strip()]


def _binance_ws_url(*, market_type: str, symbol: str) -> str:
    stream = f"{symbol.lower()}@bookTicker"
    if market_type == "spot":
        return f"wss://stream.binance.com:9443/ws/{stream}"
    return f"wss://fstream.binance.com/ws/{stream}"


def _okx_ws_url() -> str:
    # Public WS endpoint; region-specific variants exist.
    return "wss://ws.okx.com:8443/ws/v5/public"


def _bybit_ws_url(*, market_type: str) -> str:
    if market_type == "spot":
        return "wss://stream.bybit.com/v5/public/spot"
    return "wss://stream.bybit.com/v5/public/linear"


def _coinbase_ws_url() -> str:
    return "wss://ws-feed.exchange.coinbase.com"


def _deribit_ws_url() -> str:
    return "wss://www.deribit.com/ws/api/v2"


def _hyperliquid_ws_url() -> str:
    return "wss://api.hyperliquid.xyz/ws"


def _upbit_ws_url() -> str:
    return "wss://api.upbit.com/websocket/v1"


def _gate_ws_url(*, market_type: str) -> str:
    if market_type == "spot":
        return "wss://api.gateio.ws/ws/v4/"
    # USDT-settled futures
    return "wss://fx-ws.gateio.ws/v4/ws/usdt"


def _bitget_ws_url() -> str:
    return "wss://ws.bitget.com/v2/ws/public"


def _mexc_spot_ws_url() -> str:
    return "wss://wbs-api.mexc.com/ws"


def _mexc_perp_ws_url() -> str:
    return "wss://contract.mexc.com/edge"


def _xt_ws_url() -> str:
    # XT futures WS base.
    return "wss://fstream.x.group/ws/market"


def _okx_inst_id(*, market_type: str, base_symbol: str) -> str:
    # OKX uses explicit instIds for spot and swap.
    if market_type == "spot":
        return f"{base_symbol}-USDT"
    return f"{base_symbol}-USDT-SWAP"


def _bybit_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _binance_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _coinbase_product_id(*, base_symbol: str) -> str:
    return f"{base_symbol}-USD"


def _upbit_market_code(*, base_symbol: str) -> str:
    # Upbit “regional signal” is usually KRW quoted.
    return f"KRW-{base_symbol}"


def _gate_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}_USDT"


def _bitget_inst_id(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _kucoin_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}-USDT"


def _kucoin_perp_symbol(*, base_symbol: str) -> str:
    if base_symbol == "BTC":
        return "XBTUSDTM"
    return f"{base_symbol}USDTM"


def _mexc_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol}USDT"


def _mexc_contract(*, base_symbol: str) -> str:
    return f"{base_symbol}_USDT"


def _xt_symbol(*, base_symbol: str) -> str:
    return f"{base_symbol.lower()}_usdt"


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


def _derive_bbo_from_message(
    candidate: Candidate,
    payload: dict[str, Any],
    *,
    recv_ms: int,
) -> BboUpdate | None:
    venue = candidate.venue
    market_type = candidate.market_type
    base_symbol = candidate.base_symbol

    if venue == "binance":
        try:
            ts_exchange_ms = int(payload.get("E") or payload.get("T") or 0)
            bid_px = float(payload["b"])
            bid_sz = float(payload["B"])
            ask_px = float(payload["a"])
            ask_sz = float(payload["A"])
        except (KeyError, TypeError, ValueError):
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "okx":
        # Push format: {"arg": {...}, "data": [{"bidPx": "...", "bidSz": "...", "askPx": "...", "askSz": "...", "ts": "..."}]}
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        row = data[0]
        if not isinstance(row, dict):
            return None
        try:
            ts_exchange_ms = int(row.get("ts") or 0)
            bid_px = float(row["bidPx"])
            bid_sz = float(row["bidSz"])
            ask_px = float(row["askPx"])
            ask_sz = float(row["askSz"])
        except (KeyError, TypeError, ValueError):
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "bybit":
        # Perp tickers: {"topic": "tickers.BTCUSDT", "ts": <ms>, "data": {"bid1Price": "...", ...}}
        # Spot tickers do NOT include bid/ask; spot should use orderbook.1.* instead.
        topic = payload.get("topic")
        if isinstance(topic, str) and topic.startswith("orderbook."):
            # {"topic":"orderbook.1.BTCUSDT","ts":...,"type":"snapshot","data":{"b":[[px,sz]],"a":[[px,sz]],...}}
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            bids = data.get("b")
            asks = data.get("a")
            if not (isinstance(bids, list) and isinstance(asks, list) and bids and asks):
                return None
            if not (isinstance(bids[0], list) and isinstance(asks[0], list) and len(bids[0]) >= 2 and len(asks[0]) >= 2):
                return None
            bid_px = _as_float(bids[0][0])
            bid_sz = _as_float(bids[0][1])
            ask_px = _as_float(asks[0][0])
            ask_sz = _as_float(asks[0][1])
            if bid_px is None or bid_sz is None or ask_px is None or ask_sz is None:
                return None
            try:
                ts_exchange_ms = int(payload.get("ts") or 0)
            except (TypeError, ValueError):
                ts_exchange_ms = recv_ms
        else:
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            try:
                ts_exchange_ms = int(payload.get("ts") or data.get("ts") or 0)
                bid_px = float(data["bid1Price"])
                bid_sz = float(data["bid1Size"])
                ask_px = float(data["ask1Price"])
                ask_sz = float(data["ask1Size"])
            except (KeyError, TypeError, ValueError):
                return None

        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "coinbase":
        # Coinbase Exchange ticker: {"type":"ticker","product_id":"BTC-USD","best_bid":"..","best_ask":"..","time":".."}
        try:
            best_bid = float(payload["best_bid"])
            best_ask = float(payload["best_ask"])
            bid_sz = float(payload.get("best_bid_size", 0.0) or 0.0)
            ask_sz = float(payload.get("best_ask_size", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        time_str = payload.get("time")
        if isinstance(time_str, str) and time_str.endswith("Z"):
            # RFC3339/ISO8601; parse via stdlib.
            from datetime import datetime

            try:
                ts_exchange_ms = int(datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                ts_exchange_ms = recv_ms
        else:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=best_bid,
            bid_sz=bid_sz,
            ask_px=best_ask,
            ask_sz=ask_sz,
        )

    if venue == "deribit":
        # Subscription notification:
        # {"method":"subscription","params":{"channel":"book.BTC-PERPETUAL.none.1.100ms","data":{"timestamp":..,"bids":[[act,px,amt],..],"asks":[...]}}}
        if payload.get("method") != "subscription":
            return None
        params = payload.get("params")
        if not isinstance(params, dict):
            return None
        data = params.get("data")
        if not isinstance(data, dict):
            return None
        try:
            ts_exchange_ms = int(data.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts_exchange_ms = recv_ms

        # For depth=1 channels, bids/asks should include at most one level, but we handle general shape.
        best_bid_px: float | None = None
        best_bid_sz: float | None = None
        best_ask_px: float | None = None
        best_ask_sz: float | None = None

        bids = data.get("bids")
        if isinstance(bids, list):
            for item in bids:
                if not (isinstance(item, list) and len(item) >= 2):
                    continue
                try:
                    if len(item) >= 3 and isinstance(item[0], str):
                        action = str(item[0])
                        px = float(item[1])
                        amt = float(item[2])
                    else:
                        action = "new"
                        px = float(item[0])
                        amt = float(item[1])
                except (TypeError, ValueError):
                    continue
                if action == "delete" or amt <= 0:
                    continue
                if best_bid_px is None or px > best_bid_px:
                    best_bid_px = px
                    best_bid_sz = amt

        asks = data.get("asks")
        if isinstance(asks, list):
            for item in asks:
                if not (isinstance(item, list) and len(item) >= 2):
                    continue
                try:
                    if len(item) >= 3 and isinstance(item[0], str):
                        action = str(item[0])
                        px = float(item[1])
                        amt = float(item[2])
                    else:
                        action = "new"
                        px = float(item[0])
                        amt = float(item[1])
                except (TypeError, ValueError):
                    continue
                if action == "delete" or amt <= 0:
                    continue
                if best_ask_px is None or px < best_ask_px:
                    best_ask_px = px
                    best_ask_sz = amt

        if best_bid_px is None or best_ask_px is None or best_bid_sz is None or best_ask_sz is None:
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=best_bid_px,
            bid_sz=best_bid_sz,
            ask_px=best_ask_px,
            ask_sz=best_ask_sz,
        )

    if venue == "hyperliquid":
        # Channel: "bbo", data: {"time": <ms>, "bbo": [bidLevel|null, askLevel|null]}
        if payload.get("channel") != "bbo":
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        try:
            ts_exchange_ms = int(data.get("time") or 0)
        except (TypeError, ValueError):
            ts_exchange_ms = recv_ms
        bbo = data.get("bbo")
        if not (isinstance(bbo, list) and len(bbo) == 2):
            return None
        bid = bbo[0]
        ask = bbo[1]
        if not (isinstance(bid, dict) and isinstance(ask, dict)):
            return None
        try:
            bid_px = float(bid["px"])
            bid_sz = float(bid["sz"])
            ask_px = float(ask["px"])
            ask_sz = float(ask["sz"])
        except (KeyError, TypeError, ValueError):
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "gate":
        # Gate spot: {"channel":"spot.book_ticker","event":"update","result":{"t":..,"s":"BTC_USDT","b":"..","B":"..","a":"..","A":".."}}
        # Gate futures: {"channel":"futures.book_ticker","event":"update","result":{"t":..,"s":"BTC_USDT","b":"..","B":..,"a":"..","A":..}}
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        try:
            ts_exchange_ms = int(result.get("t") or payload.get("time_ms") or 0)
            bid_px = float(result.get("b") or 0)
            ask_px = float(result.get("a") or 0)
            bid_sz = float(result.get("B") or 0)
            ask_sz = float(result.get("A") or 0)
        except (TypeError, ValueError):
            return None
        if bid_px <= 0 or ask_px <= 0:
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "bitget":
        # {"arg":{...},"action":"snapshot|update","data":[{"bid1Price":"..","bid1Size":"..","ask1Price":"..","ask1Size":".."}]}
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        row = data[0]
        if not isinstance(row, dict):
            return None
        try:
            ts_exchange_ms = int(payload.get("ts") or row.get("ts") or 0)
            bid_px = float(row["bid1Price"])
            bid_sz = float(row["bid1Size"])
            ask_px = float(row["ask1Price"])
            ask_sz = float(row["ask1Size"])
        except (KeyError, TypeError, ValueError):
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "kucoin":
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        if market_type == "spot":
            # {"type":"message","topic":"/market/ticker:BTC-USDT","data":{"bestBid":"..","bestAsk":"..","bestBidSize":"..","bestAskSize":"..","time":<ms>}}
            try:
                ts_exchange_ms = int(data.get("time") or 0)
                bid_px = float(data["bestBid"])
                ask_px = float(data["bestAsk"])
                bid_sz = float(data.get("bestBidSize") or 0.0)
                ask_sz = float(data.get("bestAskSize") or 0.0)
            except (KeyError, TypeError, ValueError):
                return None
        else:
            # Futures tickerV2:
            # {"type":"message","topic":"/contractMarket/tickerV2:XBTUSDTM","data":{"bestBidPrice":"..","bestAskPrice":"..","bestBidSize":"..","bestAskSize":"..","ts":<ns>}}
            try:
                ts_ns = int(data.get("ts") or 0)
                ts_exchange_ms = ts_ns // 1_000_000 if ts_ns > 0 else 0
                bid_px = float(data["bestBidPrice"])
                ask_px = float(data["bestAskPrice"])
                bid_sz = float(data.get("bestBidSize") or 0.0)
                ask_sz = float(data.get("bestAskSize") or 0.0)
            except (KeyError, TypeError, ValueError):
                return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "mexc":
        # Spot V3 agg book ticker:
        # {"channel":"...","publicbookticker":{"bidprice":"..","bidquantity":"..","askprice":"..","askquantity":".."},"sendtime":<ms>}
        if "publicbookticker" in payload:
            book = payload.get("publicbookticker")
            if not isinstance(book, dict):
                return None
            try:
                ts_exchange_ms = int(payload.get("sendtime") or 0)
                bid_px = float(book["bidprice"])
                bid_sz = float(book["bidquantity"])
                ask_px = float(book["askprice"])
                ask_sz = float(book["askquantity"])
            except (KeyError, TypeError, ValueError):
                return None
            if ts_exchange_ms <= 0:
                ts_exchange_ms = recv_ms
            return BboUpdate(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_recv_ms=recv_ms,
                bid_px=bid_px,
                bid_sz=bid_sz,
                ask_px=ask_px,
                ask_sz=ask_sz,
            )

        # Futures ticker push:
        # {"channel":"push.ticker","data":{"bid1":6865,"ask1":6866.5,"timestamp":1587453241453,...}}
        if payload.get("channel") == "push.ticker":
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            try:
                ts_exchange_ms = int(data.get("timestamp") or 0)
                bid_px = float(data["bid1"])
                ask_px = float(data["ask1"])
            except (KeyError, TypeError, ValueError):
                return None
            if ts_exchange_ms <= 0:
                ts_exchange_ms = recv_ms
            return BboUpdate(
                candidate_id=candidate.candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                ts_exchange_ms=ts_exchange_ms,
                ts_recv_ms=recv_ms,
                bid_px=bid_px,
                bid_sz=0.0,
                ask_px=ask_px,
                ask_sz=0.0,
            )

        return None

    if venue == "xt":
        # {"topic":"agg_ticker","event":"agg_ticker@btc_usdt","data":{"t":..,"bp":"..","ap":"..","bs":"..","as":".."}}
        if payload.get("topic") != "agg_ticker":
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        try:
            ts_exchange_ms = int(data.get("t") or 0)
            bid_px = float(data["bp"])
            ask_px = float(data["ap"])
            bid_sz = float(data.get("bs") or 0.0)
            ask_sz = float(data.get("as") or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    if venue == "upbit":
        # Upbit orderbook supports simplified keys; we request DEFAULT (full) for robustness.
        try:
            ts_exchange_ms = int(payload.get("timestamp") or payload.get("tms") or 0)
        except (TypeError, ValueError):
            ts_exchange_ms = recv_ms
        units = payload.get("orderbook_units") or payload.get("obu")
        if not isinstance(units, list) or not units:
            return None
        top = units[0]
        if not isinstance(top, dict):
            return None
        ask_px = _as_float(top.get("ask_price") or top.get("ap"))
        bid_px = _as_float(top.get("bid_price") or top.get("bp"))
        ask_sz = _as_float(top.get("ask_size") or top.get("as"))
        bid_sz = _as_float(top.get("bid_size") or top.get("bs"))
        if ask_px is None or bid_px is None or ask_sz is None or bid_sz is None:
            return None
        if ts_exchange_ms <= 0:
            ts_exchange_ms = recv_ms
        return BboUpdate(
            candidate_id=candidate.candidate_id,
            venue=venue,
            market_type=market_type,
            base_symbol=base_symbol,
            ts_exchange_ms=ts_exchange_ms,
            ts_recv_ms=recv_ms,
            bid_px=bid_px,
            bid_sz=bid_sz,
            ask_px=ask_px,
            ask_sz=ask_sz,
        )

    return None


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
    connect_id = f"prefilter-{random.randrange(1_000_000_000)}"
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


async def _ws_run(
    candidate: Candidate,
    *,
    out_queue: asyncio.Queue[BboUpdate],
    stop_at_ms: int,
) -> None:
    venue = candidate.venue
    market_type = candidate.market_type
    base_symbol = candidate.base_symbol

    periodic_payload: object | None = None
    periodic_every_s = 0.0

    if venue == "binance":
        url = _binance_ws_url(market_type=market_type, symbol=_binance_symbol(base_symbol=base_symbol))
        subscribe_payload: object | None = None
    elif venue == "okx":
        url = _okx_ws_url()
        inst_id = _okx_inst_id(market_type=market_type, base_symbol=base_symbol)
        subscribe_payload = {"op": "subscribe", "args": [{"channel": "tickers", "instId": inst_id}]}
    elif venue == "bybit":
        url = _bybit_ws_url(market_type=market_type)
        sym = _bybit_symbol(base_symbol=base_symbol)
        # Bybit spot tickers don't include bid/ask; use orderbook.1 for L1.
        topic = f"orderbook.1.{sym}" if market_type == "spot" else f"tickers.{sym}"
        subscribe_payload = {"op": "subscribe", "args": [topic]}
    elif venue == "coinbase":
        url = _coinbase_ws_url()
        product = _coinbase_product_id(base_symbol=base_symbol)
        subscribe_payload = {"type": "subscribe", "product_ids": [product], "channels": ["ticker"]}
    elif venue == "deribit":
        url = _deribit_ws_url()
        # Depth=1 (top-of-book only). Interval 100ms is public; raw is restricted.
        channel = f"book.{base_symbol}-PERPETUAL.none.1.100ms"
        subscribe_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "public/subscribe",
            "params": {"channels": [channel]},
        }
    elif venue == "hyperliquid":
        url = _hyperliquid_ws_url()
        subscribe_payload = {"method": "subscribe", "subscription": {"type": "bbo", "coin": base_symbol}}
    elif venue == "upbit":
        url = _upbit_ws_url()
        code = _upbit_market_code(base_symbol=base_symbol)
        subscribe_payload = [{"ticket": f"prefilter-{candidate.candidate_id}-{base_symbol}"}, {"type": "orderbook", "codes": [code]}]
    elif venue == "gate":
        url = _gate_ws_url(market_type=market_type)
        sym = _gate_symbol(base_symbol=base_symbol)
        channel = "spot.book_ticker" if market_type == "spot" else "futures.book_ticker"
        subscribe_payload = {"time": int(time.time()), "channel": channel, "event": "subscribe", "payload": [sym]}
    elif venue == "bitget":
        url = _bitget_ws_url()
        inst_type = "spot" if market_type == "spot" else "usdt-futures"
        sym = _bitget_inst_id(base_symbol=base_symbol)
        subscribe_payload = {"op": "subscribe", "args": [{"instType": inst_type, "topic": "ticker", "symbol": sym}]}
    elif venue == "kucoin":
        url, ping_interval_s = await _kucoin_ws_info(market_type=market_type)
        sym = _kucoin_symbol(base_symbol=base_symbol) if market_type == "spot" else _kucoin_perp_symbol(base_symbol=base_symbol)
        topic = f"/market/ticker:{sym}" if market_type == "spot" else f"/contractMarket/tickerV2:{sym}"
        subscribe_payload = {
            "id": str(random.randrange(1_000_000_000)),
            "type": "subscribe",
            "topic": topic,
            "privateChannel": False,
            "response": True,
        }
        periodic_payload = {"id": str(random.randrange(1_000_000_000)), "type": "ping"}
        periodic_every_s = max(5.0, 0.8 * ping_interval_s)
    elif venue == "mexc":
        if market_type == "spot":
            url = _mexc_spot_ws_url()
            sym = _mexc_symbol(base_symbol=base_symbol)
            subscribe_payload = {"method": "SUBSCRIPTION", "params": [f"spot@public.aggre.bookTicker.v3.api.pb@100ms@{sym}"]}
        else:
            url = _mexc_perp_ws_url()
            sym = _mexc_contract(base_symbol=base_symbol)
            subscribe_payload = {"method": "sub.ticker", "param": {"symbol": sym}}
            periodic_payload = {"method": "ping"}
            periodic_every_s = 15.0
    elif venue == "xt":
        url = _xt_ws_url()
        sym = _xt_symbol(base_symbol=base_symbol)
        subscribe_payload = {"method": "subscribe", "params": [f"agg_ticker@{sym}"], "id": random.randrange(1_000_000_000)}
        periodic_payload = "ping"
        periodic_every_s = 20.0
    else:
        raise ValueError(f"Unsupported venue: {venue}")

    backoff_s = 1.0
    while _now_ms() < stop_at_ms:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=5) as ws:
                periodic_task: asyncio.Task[None] | None = None
                try:
                    if subscribe_payload is not None:
                        await ws.send(json.dumps(subscribe_payload, separators=(",", ":")))

                    if periodic_payload is not None and periodic_every_s > 0:
                        periodic_task = asyncio.create_task(
                            _send_periodic(ws, payload=periodic_payload, every_s=periodic_every_s, stop_at_ms=stop_at_ms)
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
                        # Most venues respond with dict payloads, but some can wrap messages in a list.
                        # Treat list payloads as “0..n dict events” and attempt to parse each one.
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
                            update = _derive_bbo_from_message(candidate, event, recv_ms=recv_ms)
                            if update is not None:
                                await out_queue.put(update)
                finally:
                    if periodic_task is not None:
                        periodic_task.cancel()
                        await asyncio.gather(periodic_task, return_exceptions=True)

            backoff_s = 1.0
        except (asyncio.TimeoutError, OSError, websockets.WebSocketException):
            await asyncio.sleep(backoff_s)
            backoff_s = min(10.0, backoff_s * 1.7)


def _score_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    # Rank-based “discovery capacity” score: higher update rate + tighter spreads + lower staleness.
    # This is only a permissive pre-filter ranking; it does not prove leadership.
    def f(row: dict[str, object], key: str) -> float:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        return 0.0

    def spread_p50(row: dict[str, object]) -> float:
        spread = row.get("spread_bps")
        if isinstance(spread, dict):
            p50 = spread.get("p50")
            if isinstance(p50, (int, float)):
                return float(p50)
        return 0.0

    def updates_n(row: dict[str, object]) -> int:
        updates = row.get("updates")
        if isinstance(updates, (int, float)):
            return int(updates)
        return 0

    # Group by (base_symbol, market_type) to score within comparable cohorts.
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        sym = str(row.get("base_symbol", ""))
        mkt = str(row.get("market_type", ""))
        grouped.setdefault((sym, mkt), []).append(row)

    for (_, _), items in grouped.items():
        # Rows with no data should never "win" simply because their empty spread/jitter defaults to zero.
        active = [r for r in items if updates_n(r) > 0]
        inactive = [r for r in items if r not in active]
        for r in inactive:
            r["discovery_capacity_score"] = 0.0

        if not active:
            continue

        # Compute ranks (0..1) where 1 is best.
        updates = sorted({f(r, "updates_per_s") for r in active})
        spreads = sorted({spread_p50(r) for r in active})
        stale = sorted({f(r, "stale_gap_rate_gt_1s") for r in active})

        def rank(value: float, ordered: list[float], *, higher_is_better: bool) -> float:
            if len(ordered) <= 1:
                return 1.0
            try:
                idx = ordered.index(value)
            except ValueError:
                # fallback: nearest
                idx = min(range(len(ordered)), key=lambda i: abs(ordered[i] - value))
            frac = idx / (len(ordered) - 1)
            return frac if higher_is_better else (1.0 - frac)

        for r in active:
            u = f(r, "updates_per_s")
            s = spread_p50(r)
            g = f(r, "stale_gap_rate_gt_1s")
            # Weights: updates 0.55, spread 0.30, staleness 0.15
            score = (
                0.55 * rank(u, updates, higher_is_better=True)
                + 0.30 * rank(s, spreads, higher_is_better=False)
                + 0.15 * rank(g, stale, higher_is_better=False)
            )
            r["discovery_capacity_score"] = score

    return rows


def _format_table(rows: list[dict[str, object]]) -> str:
    # Simple fixed-width table.
    def spread_p50(row: dict[str, object]) -> float:
        spread = row.get("spread_bps")
        if isinstance(spread, dict):
            value = spread.get("p50")
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    def jitter_p95(row: dict[str, object]) -> float:
        jitter = row.get("jitter_ms")
        if isinstance(jitter, dict):
            value = jitter.get("p95")
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    def f(row: dict[str, object], key: str) -> float:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        return 0.0

    header = (
        "candidate                sym  mkt   status   updates   upd/s   spr_bps(p50)  stale>1s  "
        "max_gap  jitter_p95(ms)  score"
    )
    lines = [header]
    for row in rows:
        cid = str(row.get("candidate_id", ""))[:22].ljust(22)
        sym = str(row.get("base_symbol", "")).ljust(3)
        mkt = str(row.get("market_type", "")).ljust(4)
        updates_n = int(f(row, "updates"))
        status = ("ok" if updates_n > 0 else "no_data").ljust(7)
        upd_s = f(row, "updates_per_s")
        spr = spread_p50(row)
        stale = f(row, "stale_gap_rate_gt_1s")
        max_gap = f(row, "max_gap_s")
        jit = jitter_p95(row)
        score = f(row, "discovery_capacity_score")
        lines.append(
            f"{cid}  {sym}  {mkt}  {status}  {updates_n:7d}  {upd_s:6.1f}  {spr:12.2f}  {stale:8.3f}  "
            f"{max_gap:7.2f}  {jit:13.0f}  {score:5.3f}"
        )
    return "\n".join(lines) + "\n"


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Permissive ranked L1 pre-filter: capture best bid/ask and rank venues by basic feed quality."
    )
    parser.add_argument(
        "--symbols",
        default="BTC,SOL",
        help="Comma-separated base symbols to capture (default: BTC,SOL).",
    )
    parser.add_argument(
        "--candidates",
        default="",
        help=(
            "Comma-separated candidate ids. Default uses a built-in broad list. "
            "Examples: binance_spot,coinbase_spot,okx_perp,deribit_perp."
        ),
    )
    parser.add_argument(
        "--duration-s",
        type=int,
        default=900,
        help="Capture duration in seconds (default: 900).",
    )
    parser.add_argument(
        "--out-root",
        default="logs/venue_prefilter_runs",
        help="Directory root for capture outputs (default: logs/venue_prefilter_runs).",
    )
    parser.add_argument(
        "--docs-out",
        default="docs/diagnostics",
        help="Directory for summary outputs (default: docs/diagnostics).",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Gzip the raw jsonl capture log.",
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
    out_dir = Path(args.out_root) / f"{run_id}_l1_prefilter"
    _ensure_dir(out_dir)
    docs_out = Path(args.docs_out)
    _ensure_dir(docs_out)

    raw_path = out_dir / "bbo_capture.jsonl"
    if args.gzip:
        raw_path = raw_path.with_suffix(".jsonl.gz")

    q: asyncio.Queue[BboUpdate] = asyncio.Queue(maxsize=10000)

    tasks = [asyncio.create_task(_ws_run(c, out_queue=q, stop_at_ms=stop_at_ms)) for c in candidates]

    # Stats keyed by candidate_id + symbol.
    stats: dict[tuple[str, str], CandidateStats] = {}
    for c in candidates:
        key = (c.candidate_id, c.base_symbol)
        stats[key] = CandidateStats(
            candidate_id=c.candidate_id,
            venue=c.venue,
            market_type=c.market_type,
            base_symbol=c.base_symbol,
            start_ms=start_ms,
            end_ms=start_ms,
            update_count=0,
            stale_gaps_gt_1s=0,
            stale_gaps_gt_2s=0,
            max_gap_s=0.0,
            spread_bps=Reservoir(10_000, seed=hash((c.candidate_id, c.base_symbol, "spread")) & 0xFFFFFFFF),
            jitter_ms=Reservoir(10_000, seed=hash((c.candidate_id, c.base_symbol, "jitter")) & 0xFFFFFFFF),
        )

    # Writer loop
    opener = gzip.open if args.gzip else open
    with opener(raw_path, "wt", encoding="utf-8") as handle:  # type: ignore[call-overload]
        meta = {
            "_meta": {
                "type": "venue_l1_prefilter",
                "created_at_ms": start_ms,
                "stop_at_ms": stop_at_ms,
                "symbols": base_symbols,
                "candidates": candidate_ids,
            }
        }
        handle.write(json.dumps(meta, separators=(",", ":")) + "\n")
        handle.flush()

        while _now_ms() < stop_at_ms or not q.empty():
            try:
                update = await asyncio.wait_for(q.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            key = (update.candidate_id, update.base_symbol)
            s = stats.get(key)
            if s is not None:
                s.add(update)
            handle.write(json.dumps(update.__dict__, separators=(",", ":")) + "\n")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    rows = [s.finalize() for s in stats.values()]
    rows = _score_rows(rows)
    rows.sort(
        key=lambda r: (
            str(r.get("base_symbol", "")),
            str(r.get("market_type", "")),
            -(float(score) if isinstance((score := r.get("discovery_capacity_score")), (int, float)) else 0.0),
        )
    )

    summary = _format_table(rows)
    summary_path = docs_out / f"venue-l1-prefilter-summary-{run_id}.txt"
    summary_path.write_text(summary, encoding="utf-8")

    print(f"run_dir: {out_dir}")
    print(f"raw: {raw_path}")
    print(f"summary: {summary_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
