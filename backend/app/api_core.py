"""Framework-independent HTTP API logic.

`ApiCore.dispatch(method, path, query, headers, body)` does routing, authentication, input validation
and response formatting, and returns an `ApiResponse`. The ASGI adapter (`asgi.py`) only translates
HTTP requests to/from this call, so everything security-relevant here is covered by plain unit tests.

Security properties
  * Read endpoints expose only simulated/paper data and public market data.
  * Every endpoint that does work (scan, cycle, orders) needs the `X-API-Key` header. If `API_KEY` is not
    configured those endpoints are disabled (fail closed). The key is compared in constant time.
  * There is no endpoint that changes the trading mode, the security policy or the risk limits, and none
    that bypasses the security gate. Live trading does not exist.
  * Error responses never contain stack traces, file paths, or provider URLs.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from trading_engine.orders import Order, Side

from .config import Settings
from .models.common import utcnow
from .services.office import CycleBusy, OfficeService, result_to_dict

log = logging.getLogger("office.api")

MAX_BODY_BYTES = 8192
MAX_ORDER_USD = Decimal("1000000")
_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_LOOSE_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,80}$")

_SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Dict[str, Any]
    headers: Dict[str, str] = field(default_factory=dict)


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def _err(status: int, code: str, message: str, headers: Optional[Dict[str, str]] = None) -> _HttpError:
    return _HttpError(status, code, message, headers)


def _int_param(query: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise _err(400, "invalid_parameter", f"{name} must be an integer")
    if v < lo or v > hi:
        raise _err(400, "invalid_parameter", f"{name} must be between {lo} and {hi}")
    return v


def parse_amount(value: Any, name: str = "amount") -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _err(400, "invalid_parameter", f"{name} must be a number")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise _err(400, "invalid_parameter", f"{name} must be a number")
    if not d.is_finite() or d <= 0 or d > MAX_ORDER_USD:
        raise _err(400, "invalid_parameter", f"{name} must be greater than 0 and at most {MAX_ORDER_USD}")
    return d


class ApiCore:
    def __init__(self, service: OfficeService, settings: Settings, provider_name: str,
                 version: str = "0.2.0", clock: Callable[[], datetime] = utcnow):
        self._svc = service
        self._settings = settings
        self._provider_name = provider_name
        self._version = version
        self._clock = clock
        self._strict_mint = provider_name == "live"  # real tokens must look like Solana addresses

    # ------------------------------------------------------------------
    def dispatch(self, method: str, path: str, query: Optional[Mapping[str, str]] = None,
                 headers: Optional[Mapping[str, str]] = None, body: bytes = b"") -> ApiResponse:
        try:
            hdr = {k.lower(): v for k, v in (headers or {}).items()}
            status, payload, extra = self._route(method.upper(), path, query or {}, hdr, body or b"")
            return ApiResponse(status, payload, {**_SECURITY_HEADERS, **extra})
        except _HttpError as exc:
            return ApiResponse(exc.status, {"error": {"code": exc.code, "message": exc.message}},
                               {**_SECURITY_HEADERS, **exc.headers})
        except Exception as exc:  # never leak internals
            log.error("unhandled error in %s %s: %s", method, path, type(exc).__name__)
            return ApiResponse(500, {"error": {"code": "internal_error", "message": "internal error"}},
                               dict(_SECURITY_HEADERS))

    # ------------------------------------------------------------------
    def _route(self, method: str, path: str, query: Mapping[str, str], hdr: Dict[str, str],
               body: bytes) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        p = "/" + path.strip("/")
        table = {
            "/api/health": ("GET", self._health, False),
            "/api/state": ("GET", self._state, False),
            "/api/events": ("GET", self._events, False),
            "/api/trades": ("GET", self._trades, False),
            "/api/audit/verify": ("GET", self._audit_verify, False),
            "/api/scan": ("POST", self._scan, True),
            "/api/cycle": ("POST", self._cycle, True),
            "/api/orders": ("POST", self._order, True),
        }
        if p.startswith("/api/reports/"):
            return self._need("GET", method) or self._report(p[len("/api/reports/"):])
        entry = table.get(p)
        if entry is None:
            raise _err(404, "not_found", "no such endpoint")
        allowed, handler, needs_auth = entry
        wrong = self._need(allowed, method)
        if wrong:
            return wrong
        if needs_auth:
            self._authenticate(hdr)
            return 200, handler(self._json_body(body)), {}
        return 200, handler(query), {}

    @staticmethod
    def _need(allowed: str, method: str) -> Optional[Tuple[int, Dict[str, Any], Dict[str, str]]]:
        if method != allowed:
            raise _err(405, "method_not_allowed", f"use {allowed} for this endpoint", {"Allow": allowed})
        return None

    def _authenticate(self, hdr: Dict[str, str]) -> None:
        expected = self._settings.api_key
        if not expected:
            raise _err(503, "api_key_not_configured",
                       "this endpoint is disabled until API_KEY is configured on the server")
        provided = hdr.get("x-api-key", "")
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            raise _err(401, "unauthorized", "missing or invalid API key")

    @staticmethod
    def _json_body(body: bytes) -> Dict[str, Any]:
        if len(body) > MAX_BODY_BYTES:
            raise _err(413, "payload_too_large", f"body must be at most {MAX_BODY_BYTES} bytes")
        if not body.strip():
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _err(400, "invalid_json", "body must be valid JSON")
        if not isinstance(data, dict):
            raise _err(400, "invalid_json", "body must be a JSON object")
        return data

    def _mint(self, value: Any) -> str:
        if not isinstance(value, str):
            raise _err(400, "invalid_parameter", "mint must be a string")
        rx = _BASE58 if self._strict_mint else _LOOSE_ID
        if not rx.match(value):
            raise _err(400, "invalid_parameter", "mint is not a valid token address")
        return value

    # ---- read endpoints ----------------------------------------------
    def _health(self, _q: Mapping[str, str]) -> Dict[str, Any]:
        return {"status": "ok", "version": self._version, "trading_mode": self._settings.trading_mode,
                "live_trading": False, "data_provider": self._provider_name,
                "mock_data_in_use": self._provider_name == "mock",
                "api_key_configured": bool(self._settings.api_key), "server_time": self._clock().isoformat()}

    def _rules(self) -> Dict[str, Any]:
        p, r = self._settings.policy, self._settings.risk
        return {"max_top10_holder_pct": str(p.max_top10_pct), "concentration_basis": p.concentration_basis,
                "dev_warn_pct": str(p.dev_warn_pct), "dev_block_pct": str(p.dev_block_pct),
                "min_liquidity_usd": str(p.min_liquidity_usd), "max_price_impact_pct": str(p.max_price_impact_pct),
                "block_on_mint_authority": p.block_on_mint_authority,
                "block_on_freeze_authority": p.block_on_freeze_authority,
                "max_position_pct": str(r.max_position_pct), "stop_loss_pct": str(r.stop_loss_pct),
                "max_daily_loss_pct": str(r.max_daily_loss_pct)}

    def _state(self, _q: Mapping[str, str]) -> Dict[str, Any]:
        return {"server_time": self._clock().isoformat(), "trading_mode": self._settings.trading_mode,
                "live_trading": False, "data_provider": self._provider_name,
                "mock_data_in_use": self._provider_name == "mock",
                "robots": self._svc.robots.snapshot(), "portfolio": self._svc.portfolio(),
                "reports": self._svc.reports(20), "last_cycle": self._svc.last_cycle,
                "rules": self._rules(), "latest_seq": self._svc.activity.latest_seq()}

    def _events(self, q: Mapping[str, str]) -> Dict[str, Any]:
        since = _int_param(q, "since", 0, 0, 2**62)
        limit = _int_param(q, "limit", 100, 1, 500)
        return {"events": self._svc.activity.since(since, limit), "latest_seq": self._svc.activity.latest_seq()}

    def _trades(self, q: Mapping[str, str]) -> Dict[str, Any]:
        return {"simulated": True, "trades": self._svc.broker.trades(_int_param(q, "limit", 50, 1, 500))}

    def _audit_verify(self, _q: Mapping[str, str]) -> Dict[str, Any]:
        return {"chain_valid": bool(self._svc.broker.audit.verify_chain())}

    def _report(self, mint: str) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        self._mint(mint)
        rep = self._svc.report_for(mint)
        if rep is None:
            raise _err(404, "not_found", "no security report for this token yet")
        return 200, {"report": rep}, {}

    # ---- authenticated endpoints --------------------------------------
    def _scan(self, data: Dict[str, Any]) -> Dict[str, Any]:
        mint = self._mint(data.get("mint"))
        usd = parse_amount(data["order_usd"], "order_usd") if "order_usd" in data else None
        return self._svc.scan_token(mint, usd)

    def _cycle(self, data: Dict[str, Any]) -> Dict[str, Any]:
        auto = data.get("auto_buy")
        if auto is not None and not isinstance(auto, bool):
            raise _err(400, "invalid_parameter", "auto_buy must be true or false")
        try:
            return {"cycle": self._svc.run_cycle(auto_buy=auto)}
        except CycleBusy:
            raise _err(409, "cycle_running", "a cycle is already running")

    def _order(self, data: Dict[str, Any]) -> Dict[str, Any]:
        mint = self._mint(data.get("mint"))
        side_raw = data.get("side")
        if side_raw not in ("BUY", "SELL"):
            raise _err(400, "invalid_parameter", "side must be BUY or SELL")
        amount = parse_amount(data.get("amount"), "amount")
        cid = data.get("client_order_id")
        if cid is None:
            cid = f"api-{uuid.uuid4().hex[:16]}"
        if not isinstance(cid, str) or not _LOOSE_ID.match(cid):
            raise _err(400, "invalid_parameter", "client_order_id is invalid")
        res = self._svc.place_order(Order(cid, mint, Side(side_raw), amount))
        return {"simulated": True, "order": result_to_dict(res)}
