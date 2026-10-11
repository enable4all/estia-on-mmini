"""Microsoft Graph, read-only, for the mailboxes and their calendars: fixed base URL, token per mailbox and scope.

Only URLs under https://graph.microsoft.com/v1.0/ are ever requested (delta and next links come from
Graph, but are checked anyway). Throttling (429/503/504) waits the time Graph asks for (up to `max_wait`,
`retries` times); a 401 renews the access token once; 410 Gone means a delta link expired and the folder must
be synced afresh. Mail uses the mail scopes; the calendars have their own Graph with Calendars.Read only.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

from local_assistant.mail_auth import SCOPES, AccessTokens
from local_assistant.mail_http import Http, http

GRAPH = "https://graph.microsoft.com/v1.0"
DEFAULT_WAIT = 10.0
MAX_WAIT = 120.0
_RETRY = frozenset({429, 503, 504})


class GraphError(Exception):
    """Graph refused or failed; the message holds the status and Graph's error code, never content."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status  # the HTTP status (0: not an HTTP answer)


class DeltaExpired(GraphError):
    """410 Gone: the delta link is no longer valid; sync the folder from scratch."""


class Graph:
    def __init__(
        self,
        tokens: AccessTokens,
        *,
        http: Http = http,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retries: int = 3,
        scope: str = SCOPES,
        max_wait: float = MAX_WAIT,
    ) -> None:
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self._retries = retries
        self._scope = scope
        self._max_wait = max_wait

    async def get(self, address: str, path_or_url: str, *, prefer: str = "") -> dict[str, Any]:
        """GET a Graph path ("/me/...") or a Graph link; JSON object back, GraphError otherwise."""
        url = path_or_url if path_or_url.startswith("https://") else GRAPH + path_or_url
        if not url.startswith(GRAPH + "/"):
            raise GraphError("refusing a URL outside Microsoft Graph")
        renewed = False
        attempt = 0
        while True:
            token = await self._tokens.get(address, self._scope)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if prefer:
                headers["Prefer"] = prefer
            result = await self._http("GET", url, headers, None)
            if result.status == 200 and isinstance(result.body, dict):
                return result.body
            if result.status == 401 and not renewed:
                self._tokens.forget(address, self._scope)
                renewed = True
                continue
            if result.status == 410:
                raise DeltaExpired("Graph HTTP 410: the delta link expired", 410)
            if result.status in _RETRY and attempt < self._retries:
                attempt += 1
                await self._sleep(min(self._max_wait, _retry_after(result.headers.get("retry-after"))))
                continue
            code = result.body.get("error", {}).get("code", "") if isinstance(result.body, dict) else ""
            raise GraphError(f"Graph HTTP {result.status} {code}".strip(), result.status)

    async def message_text(self, address: str, graph_id: str) -> dict[str, Any]:
        """One message with its body as plain text (fetched live, never stored)."""
        path = (
            f"/me/messages/{urllib.parse.quote(graph_id, safe='')}"
            "?$select=subject,from,toRecipients,receivedDateTime,body,internetMessageHeaders"
        )
        return await self.get(address, path, prefer='outlook.body-content-type="text"')

    async def search(self, address: str, words: str, top: int = 10) -> list[dict[str, Any]]:
        """Live full-text search in the mailbox (subject, body, people) for words in bodies."""
        phrase = urllib.parse.quote(f'"{words.replace(chr(34), "")}"', safe="")
        path = (
            f"/me/messages?$search={phrase}&$top={top}"
            "&$select=id,conversationId,from,subject,receivedDateTime,parentFolderId"
        )
        found = (await self.get(address, path)).get("value", [])
        return [item for item in found if isinstance(item, dict)]


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value) if value else DEFAULT_WAIT
    except ValueError:
        seconds = DEFAULT_WAIT
    return max(1.0, min(MAX_WAIT, seconds))
