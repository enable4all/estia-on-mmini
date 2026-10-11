"""HTTPS for Microsoft sign-in and Graph: standard library only, no proxies, JSON in and out."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

TIMEOUT = 30.0
MAX_BODY = 20 * 1024 * 1024


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: Any  # parsed JSON, or None when the answer is empty or not JSON


Http = Callable[[str, str, dict[str, str], bytes | None], Awaitable[HttpResult]]


class TimedHttp(Protocol):
    """`http` below: one request that stops itself after `socket_timeout` seconds."""

    async def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, *, socket_timeout: float = ...
    ) -> HttpResult: ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect comes back as its own result: urllib would send the bearer token along to the new address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


async def http(
    method: str, url: str, headers: dict[str, str], body: bytes | None, *, socket_timeout: float = TIMEOUT
) -> HttpResult:
    """One request; HTTP error statuses come back as results, network failures raise OSError. `socket_timeout` ends the
    request itself (a caller's wait_for alone would leave it running in its thread)."""

    def call() -> HttpResult:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with opener.open(request, timeout=socket_timeout) as response:
                return _result(response.status, dict(response.headers), response.read(MAX_BODY))
        except urllib.error.HTTPError as exc:
            return _result(exc.code, dict(exc.headers or {}), exc.read(MAX_BODY))

    return await asyncio.to_thread(call)


def _result(status: int, headers: dict[str, str], raw: bytes) -> HttpResult:
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = None
    return HttpResult(status, {k.lower(): v for k, v in headers.items()}, body)
