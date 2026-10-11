"""One guarded download for learn_link, reduced to readable text.

Guards (spec section 7): http(s) only; hosts are resolved to IPv4 only (the home LAN has global
IPv6 addresses that would pass a "public" check) and every address must be public (no loopback,
private, link-local, CGNAT/Tailscale, multicast); the connection goes to the checked address, so
DNS cannot change the answer in between; the same checks after every redirect; at most
3 redirects, 2 MB, 15 s per network step and 30 s in total.
"""

from __future__ import annotations

import codecs
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit

USER_AGENT = "local-assistant/0.1 (private assistant; fetches pages its user sends)"
ALLOWED_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain", "application/json", "application/ld+json"}
)
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_LOGIN_PATHS = ("/authwall", "/login", "/signin", "/uas/login", "/checkpoint")
# Chat-template control tokens in page text could be read as real turn markers by the model.
_CONTROL_TOKENS = re.compile(
    r"<\|[^<>|]{0,40}\|?>|<[A-Za-z_\"]{1,40}\|>"
    r"|<\s*/?\s*(?:start_of_turn|end_of_turn|start_of_image|end_of_image|bos|eos|pad)\s*>",
    re.IGNORECASE,
)
_INVISIBLE = frozenset({"Cc", "Cf", "Co", "Cs"})  # control, format (bidi, zero-width), private use, surrogates


def _clean_title(title: str, limit: int = 200) -> str:
    """A page title as one clean line (it reaches Gemma, the history and the /learn reply)."""
    text = " ".join(strip_control_tokens(title[: limit * 4]).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def strip_control_tokens(text: str) -> str:
    """Outside text (pages, mail) without chat-template tokens or hidden characters that could forge a turn in
    Gemma's input or hide what it says; line breaks and tabs stay."""
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in _INVISIBLE)
    for _ in range(3):  # hidden characters first, then tokens: no token can be rebuilt from pieces
        cleaned = _CONTROL_TOKENS.sub("", text)
        if cleaned == text:
            return cleaned
        text = cleaned
    return text.replace("<", "").replace(">", "")  # nested deeper than real text ever is: drop the brackets (linear)


Resolver = Callable[[str, int], list[str]]


class FetchError(Exception):
    """The link could not be read. The message is safe to show the user."""


@dataclass(frozen=True)
class Page:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str


def is_public(ip: str) -> bool:
    address = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_multicast or not address.is_global:
        return False
    return not (isinstance(address, ipaddress.IPv4Address) and address in _CGNAT)


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"host not found: {host}") from exc
    return sorted({str(info[4][0]) for info in infos})


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, *, ip: str, timeout: float, context: ssl.SSLContext | None = None) -> None:
        self._tls = context or ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._tls)
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)


class _PageParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "section",
            "article",
            "header",
            "footer",
            "ul",
            "ol",
            "table",
            "blockquote",
            "pre",
            "dd",
            "dt",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.jsonld: list[str] = []
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._jsonld_buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self._jsonld_buffer = []
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._jsonld_buffer is not None:
            self.jsonld.append("".join(self._jsonld_buffer))
            self._jsonld_buffer = None
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._jsonld_buffer is not None:
            self._jsonld_buffer.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _collapse(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _content_type(header: str) -> tuple[str, str]:
    media, _, params = header.partition(";")
    charset = "utf-8"
    for param in params.split(";"):
        key, _, value = param.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip().strip('"')
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = "utf-8"
    return media.strip().lower(), charset


def _decode(body: bytes, charset: str) -> str:
    """Decode with the declared charset; non-text codecs (base64, zlib ...) fall back to UTF-8."""
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [node for item in value for node in _nodes(item)]
    if isinstance(value, dict):
        return [value, *_nodes(value.get("@graph"))]
    return []


_TAG = re.compile(r"<[^>]+>")


def _clean(value: str) -> str:
    """JSON-LD values can carry HTML (LinkedIn's About uses <br> and entities): plain text only."""
    return " ".join(html.unescape(_TAG.sub(" ", value)).split())


def _names(value: Any) -> list[str]:
    names: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and _clean(item):
            names.append(_clean(item))
        elif isinstance(item, dict) and isinstance(item.get("name"), str) and _clean(item["name"]):
            names.append(_clean(item["name"]))
    return names


def _with_years(value: Any) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        member = item.get("member")
        years = ""
        if isinstance(member, dict) and member.get("startDate"):
            years = f" ({member['startDate']}–{member.get('endDate') or ''})"
        out.append(_clean(item["name"]) + years)
    return out


def _render_person(person: dict[str, Any]) -> str:
    lines: list[str] = []

    def add(label: str, values: list[str]) -> None:
        if values:
            lines.append(f"{label}: {', '.join(values)}")

    add("Name", _names(person.get("name")))
    add("About", _names(person.get("description")))
    add("Job titles", _names(person.get("jobTitle")))
    add("Works for", _with_years(person.get("worksFor")))
    add("Education", _with_years(person.get("alumniOf")))
    address = person.get("address")
    if isinstance(address, dict):
        add(
            "Location",
            [v for v in (address.get("addressLocality"), address.get("addressCountry")) if isinstance(v, str) and v],
        )
    add("Languages", _names(person.get("knowsLanguage")))
    add("Awards", _names(person.get("awards")))
    add("Member of", _names(person.get("memberOf")))
    return "\n".join(lines)


def _person_from_jsonld(blocks: list[str], page_url: str) -> str:
    """The page owner's Person data (LinkedIn public profiles carry it as JSON-LD), or ''."""
    persons: list[dict[str, Any]] = []
    for block in blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, RecursionError):
            continue
        persons.extend(node for node in _nodes(data) if node.get("@type") == "Person")
    if not persons:
        return ""
    path = urlsplit(page_url).path.rstrip("/")

    def is_owner(person: dict[str, Any]) -> bool:
        return any(
            isinstance(person.get(key), str) and urlsplit(person[key]).path.rstrip("/") == path
            for key in ("url", "sameAs")
        )

    owner = next((p for p in persons if is_owner(p)), None)
    if owner is None:
        owner = next((p for p in persons if p.get("jobTitle") or p.get("description")), persons[0])
    return _render_person(owner)


def _looks_like_login(parts: SplitResult) -> bool:
    path = parts.path.lower()
    return any(path.startswith(prefix) for prefix in _LOGIN_PATHS)


class Fetcher:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        *,
        ip_check: Callable[[str], bool] = is_public,
        max_bytes: int = 2_000_000,
        timeout: float = 15.0,
        total_timeout: float = 30.0,
        max_redirects: int = 3,
        max_text_chars: int = 21_000,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._resolver = resolver
        self._ip_check = ip_check
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._total_timeout = total_timeout
        self._max_redirects = max_redirects
        self._max_text_chars = max_text_chars
        self._user_agent = user_agent

    def fetch(self, url: str) -> Page:
        current = url.strip()
        deadline = time.monotonic() + self._total_timeout
        for _hop in range(self._max_redirects + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                raise FetchError("only http and https links can be read")
            if parts.username or parts.password:
                raise FetchError("links with a user name or password are not allowed")
            if not parts.hostname:
                raise FetchError("the link has no host")
            if _looks_like_login(parts):
                raise FetchError("the page needs a login (login wall)")
            try:
                port = parts.port or (443 if parts.scheme == "https" else 80)
            except ValueError as exc:
                raise FetchError("the link has an invalid port") from exc
            addresses = self._resolver(parts.hostname, port)
            if not addresses:
                raise FetchError(f"host not found: {parts.hostname}")
            if not all(self._ip_check(address) for address in addresses):
                raise FetchError(f"{parts.hostname} is not a public internet address")
            status, headers, body = self._get(parts, addresses[0], port, deadline)
            if status in _REDIRECTS:
                location = headers.get("location")
                if not location:
                    raise FetchError("redirect without a target")
                current = urljoin(current, location)
                continue
            if status != 200:
                raise FetchError(f"the site answered HTTP {status}")
            content_type, charset = _content_type(headers.get("content-type", ""))
            if content_type not in ALLOWED_TYPES:
                raise FetchError(f"unsupported content type {content_type or 'unknown'}")
            return self._page(url.strip(), current, content_type, _decode(body, charset))
        raise FetchError("too many redirects")

    def _get(self, parts: SplitResult, ip: str, port: int, deadline: float) -> tuple[int, dict[str, str], bytes]:
        host = parts.hostname or ""
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        conn: http.client.HTTPConnection
        if parts.scheme == "https":
            conn = _PinnedHTTPSConnection(host, port, ip=ip, timeout=self._timeout)
        else:
            conn = _PinnedHTTPConnection(host, port, ip=ip, timeout=self._timeout)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "Accept-Language": "en",
                },
            )
            response = conn.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            chunks: list[bytes] = []
            size = 0
            while size <= self._max_bytes:
                if time.monotonic() > deadline:
                    raise FetchError(f"the site was too slow (over {self._total_timeout:.0f} s)")
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            body = b"".join(chunks)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f"download failed: {exc}") from exc
        finally:
            conn.close()
        if len(body) > self._max_bytes:
            raise FetchError(f"the page is too big (over {self._max_bytes:,} bytes)")
        return response.status, headers, body

    def _page(self, requested: str, final: str, content_type: str, text: str) -> Page:
        title = ""
        body = text
        if content_type in _HTML_TYPES:
            parser = _PageParser()
            parser.feed(text)
            parser.close()
            title = _clean_title(parser.title)  # outside text too: tokens, hidden characters, one line
            body = _person_from_jsonld(parser.jsonld, final) or parser.text()
        body = _collapse(strip_control_tokens(body))[: self._max_text_chars]
        if not body:
            raise FetchError("the page has no readable text")
        return Page(url=requested, final_url=final, title=title, text=body, content_type=content_type)
