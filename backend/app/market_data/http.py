"""Minimal, defensive JSON-over-HTTP client for market-data providers (standard library only).

  * https only (plain http is allowed for localhost, used by tests and local validators)
  * redirects are never followed (a redirect could forward credentials to another host)
  * response size is capped and every request has a timeout
  * every failure becomes `ProviderUnavailable` with a short generic message: the URL, query string and
    headers (which may carry API keys) are never included
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional, Protocol
from urllib.parse import urlparse

from .base import ProviderUnavailable

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ProviderNotFound(ProviderUnavailable):
    """The provider answered 404: the resource does not exist (distinct from an outage)."""


class JsonHttp(Protocol):
    def get_json(self, url: str, headers: Optional[Mapping[str, str]] = None, timeout: float = 10.0) -> Any: ...

    def post_json(self, url: str, body: Any, headers: Optional[Mapping[str, str]] = None,
                  timeout: float = 10.0) -> Any: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None  # turns the 3xx into an HTTPError


def check_url(url: str) -> None:
    parts = urlparse(url)
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return
    if parts.scheme == "http" and host in LOCAL_HOSTS:
        return
    raise ProviderUnavailable("refusing to call a non-https URL")


class UrllibJsonHttp:
    def __init__(self, max_bytes: int = 2_000_000, user_agent: str = "ai-crypto-trading-office/0.2",
                 opener: Optional[urllib.request.OpenerDirector] = None):
        self._max = max_bytes
        self._ua = user_agent
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def get_json(self, url: str, headers: Optional[Mapping[str, str]] = None, timeout: float = 10.0) -> Any:
        return self._send(url, None, headers, timeout)

    def post_json(self, url: str, body: Any, headers: Optional[Mapping[str, str]] = None,
                  timeout: float = 10.0) -> Any:
        return self._send(url, json.dumps(body).encode("utf-8"), headers, timeout)

    def _send(self, url: str, data: Optional[bytes], headers: Optional[Mapping[str, str]], timeout: float) -> Any:
        check_url(url)
        hdr: Dict[str, str] = {"Accept": "application/json", "User-Agent": self._ua}
        if data is not None:
            hdr["Content-Type"] = "application/json"
        hdr.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdr, method="POST" if data is not None else "GET")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read(self._max + 1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code == 404:
                raise ProviderNotFound("not found") from None
            if code == 429:
                raise ProviderUnavailable("rate limited") from None
            if code in (401, 403):
                raise ProviderUnavailable("access denied (check API key / allowlist)") from None
            raise ProviderUnavailable(f"HTTP {code}") from None
        except (socket.timeout, TimeoutError):
            raise ProviderUnavailable("timeout") from None
        except ssl.SSLError:
            raise ProviderUnavailable("TLS error") from None
        except (urllib.error.URLError, OSError):
            raise ProviderUnavailable("network error") from None
        if len(raw) > self._max:
            raise ProviderUnavailable("response too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProviderUnavailable("response was not valid JSON") from None
