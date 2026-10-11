"""Web search for Gemma's web_search tool: Ollama's web search first (it returns the pages' text), Brave's web
search as the backup (short descriptions, so its top page is read for more).

Results are data from the web, never instructions, and never stored or cached (Brave's terms allow transient use
only). The keys live only in `web.env` (0600, written by
scripts/set-web-keys.sh), read per search. A service that says "too many requests" or refuses the key is paused for
a while; Brave gets at most one request a second (its free plan's limit).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from local_assistant.calendar_tools import clean
from local_assistant.fetcher import Page
from local_assistant.keyfile import read_keys
from local_assistant.mail_http import TimedHttp
from local_assistant.part_status import PartStatus, Reason
from local_assistant.store import Store

log = logging.getLogger(__name__)

OLLAMA_URL = "https://ollama.com/api/web_search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RESULTS = 5
QUICK_RESULTS = 3  # on a call
TITLE_CHARS = 150
RESULT_CHARS = 1_200
PAGE_CHARS = 2_000  # from Brave's top page, added to its description
QUERY_CHARS = 400  # Brave's limits: 400 characters, 50 words
QUERY_WORDS = 50
SOURCES_PER_SEARCH = 3
MAX_SOURCES = 5
BUSY_PAUSE_SECONDS = 600.0  # after "too many requests"
REFUSED_PAUSE_SECONDS = 3600.0  # after 401/402/403: the key or the plan was refused
MONTH_PAUSE_MAX_SECONDS = 24 * 3600.0  # Brave out of searches for the month: look again a day later at most
BRAVE_GAP_SECONDS = 1.1  # Brave's free plan: one request a second
NO_KEYS = "web search has no keys: the user runs scripts/set-web-keys.sh"
_MARKDOWN_LINK = re.compile(r"!?\[([^\[\]]*)\]\([^()]*\)")  # no nested brackets: linear on "[[[[…"
_TAG = re.compile(r"<[^<>]{1,20}>")  # Brave's <strong> highlights
_HOST = re.compile(r"[a-z0-9.-]+")
_ODD = frozenset({"Cc", "Cf", "Co", "Cs", "Zs", "Zl", "Zp"})  # hidden, control and space characters
_DATE = re.compile(r"\d{4}-\d\d-\d\d")
# private details that never go into a query (an answer's private text could otherwise leave the house)
_PRIVATE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")  # an email address
_LONG_NUMBER = re.compile(r"(?:\d[\s().-]?){9,}")  # phone, account or card numbers (a date has 8 digits)
_IBAN = re.compile(r"\b[A-Za-z]{2}\d{2}[A-Za-z0-9]{10,30}\b")

Fetch = Callable[[str], Awaitable[Page]]
Sleep = Callable[[float], Awaitable[None]]


class WebSearchError(Exception):
    pass


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    site: str  # host without "www."
    date: str  # YYYY-MM-DD when the provider knows it, else ""
    text: str


@dataclass(frozen=True)
class WebResults:
    provider: str  # "Ollama" or "Brave"
    results: tuple[WebResult, ...]


@dataclass(frozen=True)
class Limits:
    """Seconds per request; on a call ("quick") the whole turn has about 45 s."""

    ollama_s: float = 8.0
    brave_s: float = 5.0
    ollama_quick_s: float = 5.0
    brave_quick_s: float = 3.0
    page_s: float = 6.0


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class _Answer:
    status: int  # 0: no answer (network error, timeout)
    headers: dict[str, str]
    items: list[tuple[object, object, str, str]] | None  # title, url, date, text; None: unreadable or failed


class Searcher(Protocol):
    async def search(self, query: str, *, quick: bool = False) -> WebResults: ...


def _this_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class WebSearch:
    def __init__(
        self,
        http: TimedHttp,
        keys_path: Path,
        store: Store,
        *,
        country: str,
        brave_cap: int,
        month: Callable[[], str] = _this_month,
        day: Callable[[], str] = _today,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        fetch: Fetch | None = None,
        limits: Limits = DEFAULT_LIMITS,
        status: PartStatus | None = None,
    ) -> None:
        self._http = http
        self._keys_path = keys_path
        self._store = store
        self._country = country
        self._brave_cap = brave_cap
        self._month = month
        self._day = day
        self._clock = clock
        self._sleep = sleep
        self._fetch = fetch
        self._limits = limits
        self._paused: dict[str, tuple[float, str]] = {}  # provider → (until, why)
        self._brave_last = -math.inf
        self._status = status or PartStatus(None)  # what the last real search did (doctor, /status)
        self._problem: tuple[Reason, int] | None = None  # why the last request of this search failed

    async def search(self, query: str, *, quick: bool = False) -> WebResults:
        """Up to 5 results (3 on a call) for the query; ValueError for a bad query, WebSearchError when no service
        answers. `quick`: on a call, with shorter limits and no page reading."""
        query = " ".join(query.split())
        if not query:
            raise ValueError("the search words are empty.")
        if len(query) > QUERY_CHARS or len(query.split()) > QUERY_WORDS:
            raise ValueError(f"the search words are too long (at most {QUERY_CHARS} characters, {QUERY_WORDS} words).")
        if has_private_details(query):
            raise ValueError(
                "not searched: the search words hold an email address, an account number (IBAN) or a long number such "
                "as a phone number, and private details never go to a search engine. Search without them."
            )
        keys = self._keys()
        ollama, brave = keys.get("OLLAMA_API_KEY", ""), keys.get("BRAVE_API_KEY", "")
        if not brave and not ollama:
            self._status.failed("web", "no_key")
            raise WebSearchError(NO_KEYS)
        self._problem = None
        found = await self._first_answer(query, ollama, brave, quick)
        if found is not None:
            self._status.ok("web")
            return found
        if self._problem is not None:  # a service was asked and failed (not only paused ones skipped)
            self._status.failed("web", *self._problem)
        raise WebSearchError("web search is not available right now")

    async def _first_answer(self, query: str, ollama: str, brave: str, quick: bool) -> WebResults | None:
        """Ollama first, Brave as the backup; None when no service answered."""
        count = QUICK_RESULTS if quick else MAX_RESULTS
        answered = ""  # a service that answered, if only with nothing
        if ollama and not self._is_paused("Ollama"):
            found = await self._ollama(query, ollama, count, quick)
            if found:
                return WebResults("Ollama", found)
            answered = "Ollama" if found is not None else answered
        if brave and self._brave_used() < self._brave_cap and not self._is_paused("Brave"):
            found = await self._brave(query, brave, count, quick)
            if found:
                if not quick:
                    found = await self._read_top_page(found, query)
                return WebResults("Brave", found)
            answered = "Brave" if found is not None else answered
        return WebResults(answered, ()) if answered else None

    def usage(self) -> str:
        """One status line: searches today (Ollama) and this month (Brave), keys, and paused services."""
        keys = self._keys()
        ollama, brave = bool(keys.get("OLLAMA_API_KEY")), bool(keys.get("BRAVE_API_KEY"))
        if not ollama and not brave:
            return "keys missing (scripts/set-web-keys.sh)"
        today = f"Ollama {self._counted(f'web_ollama_{self._day()}')} today"
        month = f"{self._brave_used()}/{self._brave_cap} this month"
        if ollama and brave:
            line = f"{today}, Brave backup {month}"
        elif ollama:
            line = f"{today} (no Brave key)"
        else:
            line = f"Brave {month} (no Ollama key)"
        now = self._clock()
        for provider, (until, why) in sorted(self._paused.items()):
            if until > now:
                line += f"; {provider} paused {math.ceil((until - now) / 60)} min ({why})"
        return line

    def _is_paused(self, provider: str) -> bool:
        until = self._paused.get(provider, (0.0, ""))[0]
        return until > self._clock()

    def _pause(self, provider: str, seconds: float, why: str) -> None:
        self._paused[provider] = (self._clock() + seconds, why)
        log.warning("web search: %s paused for %d min (%s)", provider, math.ceil(seconds / 60), why)

    async def _ollama(self, query: str, key: str, count: int, quick: bool) -> tuple[WebResult, ...] | None:
        self._count(f"web_ollama_{self._day()}")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        body = json.dumps({"query": query, "max_results": count}).encode()
        limit = self._limits.ollama_quick_s if quick else self._limits.ollama_s
        answer = await self._ask("Ollama", "POST", OLLAMA_URL, headers, body, limit, _ollama_items)
        if answer.status == 429:
            self._pause("Ollama", BUSY_PAUSE_SECONDS, "too many requests")
        elif answer.status in (401, 402, 403):
            self._pause("Ollama", REFUSED_PAUSE_SECONDS, f"key refused, HTTP {answer.status}")
        return _results(answer.items, query, count)

    async def _brave(self, query: str, key: str, count: int, quick: bool) -> tuple[WebResult, ...] | None:
        params = {
            "q": query,
            "count": count,
            "country": self._country,
            "search_lang": "en",
            "safesearch": "moderate",
            "extra_snippets": "true",  # ignored on the free plan
            "text_decorations": "false",
        }
        url = f"{BRAVE_URL}?{urlencode(params)}"
        headers = {"Accept": "application/json", "X-Subscription-Token": key}
        limit = self._limits.brave_quick_s if quick else self._limits.brave_s
        for attempt in (1, 2):
            wait = self._brave_last + BRAVE_GAP_SECONDS - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._count(self._month_key())  # a failed request may still count
            answer = await self._ask("Brave", "GET", url, headers, None, limit, _brave_items)
            self._brave_last = self._clock()
            if answer.status in (401, 402, 403):
                self._pause("Brave", REFUSED_PAUSE_SECONDS, f"key refused, HTTP {answer.status}")
            if answer.status != 429:
                return _results(answer.items, query, count)
            per_month = _pair(answer.headers.get("x-ratelimit-remaining", ""))[1]
            if per_month == 0:
                reset = _pair(answer.headers.get("x-ratelimit-reset", ""))[1]
                pause = min(float(reset), MONTH_PAUSE_MAX_SECONDS) if reset else MONTH_PAUSE_MAX_SECONDS
                self._pause("Brave", pause, "no searches left this month")
                return None
            if attempt == 1 and not quick:  # one a second, shared with other tools: wait and try once more
                await self._sleep(BRAVE_GAP_SECONDS)
            else:
                return None  # on a call there is no time to wait
        return None

    async def _read_top_page(self, found: tuple[WebResult, ...], query: str) -> tuple[WebResult, ...]:
        """Brave gives one-line descriptions: the top result's page (through the guarded fetcher) adds the part
        that matches the question. A page that cannot be read keeps the description."""
        if self._fetch is None:
            return found
        top = found[0]
        try:
            page = await asyncio.wait_for(self._fetch(top.url), self._limits.page_s)
        except Exception as exc:  # FetchError, a timeout or anything else: the description stays
            log.info("web search: Brave's top page not read: %s", type(exc).__name__)
            return found
        part = clean(_MARKDOWN_LINK.sub(r"\1", _best_part(page.text, query, PAGE_CHARS)), PAGE_CHARS)
        if not part:
            return found
        text = f"{top.text} … {part}" if top.text else part
        return (WebResult(top.title, top.url, top.site, top.date, text), *found[1:])

    def _keys(self) -> dict[str, str]:
        return read_keys(self._keys_path, ("BRAVE_API_KEY", "OLLAMA_API_KEY"))

    def _month_key(self) -> str:
        return f"web_brave_{self._month()}"

    def _brave_used(self) -> int:
        return self._counted(self._month_key())

    def _counted(self, key: str) -> int:
        try:
            return int(self._store.get_state(key) or 0)
        except ValueError:
            return 0

    def _count(self, key: str) -> None:
        self._store.set_state(key, str(self._counted(key) + 1))

    async def _ask(
        self,
        provider: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        limit: float,
        parse: Callable[[Any], list[tuple[object, object, str, str]] | None],
    ) -> _Answer:
        """One request; never raises (logged as metadata only)."""
        try:
            answer = await asyncio.wait_for(self._http(method, url, headers, body, socket_timeout=limit), limit)
            items = parse(answer.body) if answer.status == 200 else None
        except Exception as exc:  # any failure (network, timeout, an unreadable body) means: try the next service
            log.warning("web search: %s failed: %s", provider, type(exc).__name__)
            self._problem = ("no_answer", 0)
            return _Answer(0, {}, None)
        if answer.status != 200:
            log.warning("web search: %s answered HTTP %d", provider, answer.status)
            self._problem = ("busy" if answer.status == 429 else "refused", answer.status)
        elif items is None:
            log.warning("web search: %s gave an unreadable answer", provider)
            self._problem = ("bad_answer", 0)
        elif not items:
            log.info("web search: %s found nothing", provider)
        return _Answer(answer.status, answer.headers, items)


def has_private_details(text: str) -> bool:
    """An email address, an IBAN or a run of 9+ digits (phone, account, card numbers): never sent to a service."""
    return bool(_PRIVATE.search(text) or _LONG_NUMBER.search(text) or _IBAN.search(text.replace(" ", "")))


def _pair(value: str) -> tuple[int | None, int | None]:
    """Brave's "1, 1996" rate-limit headers: (per second, per month)."""
    parts = [part.strip() for part in value.split(",")]
    numbers = [int(part) if part.isdigit() else None for part in parts]
    return (numbers[0] if numbers else None, numbers[1] if len(numbers) > 1 else None)


def _best_part(text: str, query: str, size: int) -> str:
    """The `size` characters of a long text that best match the query's words and numbers (a page's answer is
    rarely in its first lines), starting a little before the first match."""
    text = text[:50_000]
    if len(text) <= size:
        return text
    terms = {word for word in re.findall(r"[a-z0-9]+", query.casefold()) if len(word) >= 3 or word.isdigit()}
    low = text.casefold()
    best, best_score = 0, 0
    for start in range(0, len(text) - size + 1, max(size // 4, 100)):
        window = low[start : start + size]
        score = sum(window.count(term) for term in terms)
        if score > best_score:
            best, best_score = start, score
    if best_score == 0:
        return text[:size]
    window = low[best : best + size]
    first = min(pos for pos in (window.find(term) for term in terms) if pos >= 0)
    start = max(0, min(best + first - size // 4, len(text) - size))
    return text[start : start + size]


def _site(url: str) -> str | None:
    """The link's host for the Sources note (punycode, without "www."); None for a link that could fake a line,
    hide text or carry anything but a plain web address."""
    if any(unicodedata.category(ch) in _ODD or ch in '<>"\\' for ch in url):
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        host = parts.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host.removeprefix("www.") if _HOST.fullmatch(host) else None


def _results(
    items: list[tuple[object, object, str, str]] | None, query: str, count: int
) -> tuple[WebResult, ...] | None:
    """Checked, cleaned results (the part of each text that best matches the query); None stays None."""
    if items is None:
        return None
    found: list[WebResult] = []
    for title, url, date, text in items:
        if not isinstance(url, str) or len(url) > 2_000:
            continue
        url = url.strip()
        site = _site(url)
        if site is None:
            continue
        raw_title = title[: TITLE_CHARS * 4] if isinstance(title, str) else ""  # cut first: the cleaning stays cheap
        name = clean(_MARKDOWN_LINK.sub(r"\1", _TAG.sub("", raw_title)), TITLE_CHARS)
        part = _best_part(_TAG.sub("", text), query, RESULT_CHARS * 2)
        body = clean(_MARKDOWN_LINK.sub(r"\1", part), RESULT_CHARS)
        found.append(WebResult(name or site, url, site, date if _DATE.fullmatch(date) else "", body))
        if len(found) == count:
            break
    return tuple(found)


def _brave_items(body: Any) -> list[tuple[object, object, str, str]] | None:
    if not isinstance(body, dict):
        return None
    web = body.get("web")
    if web is None:
        return [] if body.get("type") == "search" else None  # Brave leaves "web" out when nothing matched
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return None
    items: list[tuple[object, object, str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        texts = [item.get("description")] + (item.get("extra_snippets") or [])
        text = " … ".join(t for t in texts if isinstance(t, str) and t.strip())
        age = item.get("page_age")
        items.append((item.get("title"), item.get("url"), age[:10] if isinstance(age, str) else "", text))
    return items


def _ollama_items(body: Any) -> list[tuple[object, object, str, str]] | None:
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        return None
    items: list[tuple[object, object, str, str]] = []
    for item in results:
        if isinstance(item, dict):
            content = item.get("content")
            items.append((item.get("title"), item.get("url"), "", content if isinstance(content, str) else ""))
    return items


def sources(searches: Sequence[WebResults]) -> list[tuple[str, str]]:
    """The links for the Sources note: the first 3 of each search, without repeats, at most 5."""
    links: list[tuple[str, str]] = []
    for search in searches:
        for result in search.results[:SOURCES_PER_SEARCH]:
            if all(url != result.url for _, url in links):
                links.append((result.site, result.url))
    return links[:MAX_SOURCES]
