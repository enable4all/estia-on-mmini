import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from local_assistant.fetcher import FetchError, Page
from local_assistant.mail_http import HttpResult
from local_assistant.part_status import PartStatus, Record
from local_assistant.store import Store
from local_assistant.web_search import (
    BRAVE_URL,
    OLLAMA_URL,
    Limits,
    WebResult,
    WebResults,
    WebSearch,
    WebSearchError,
    sources,
)
from tests.fakes.fake_http import FakeHttp

OLLAMA_ANSWER = {
    "results": [
        {"title": "Ajax - PSV", "url": "https://www.ajax.nl/news", "content": "Ajax beat PSV 2-1."},
    ]
}
BRAVE_ANSWER = {
    "type": "search",
    "web": {
        "results": [
            {
                "title": "Ajax wint van <strong>PSV</strong>",
                "url": "https://nos.nl/artikel/1",
                "description": "Ajax won <strong>2-1</strong> on Sunday.",
                "extra_snippets": ["The winner came late.", 5],
                "page_age": "2026-10-09T18:00:00",
            },
            {"title": "Ajax 2-1 PSV", "url": "https://www.espn.com/soccer/2", "description": "Match report."},
        ]
    },
}
AJAX = WebResult("Ajax - PSV", "https://www.ajax.nl/news", "ajax.nl", "", "Ajax beat PSV 2-1.")


def keys(tmp_path: Path, text: str = "BRAVE_API_KEY=b-key\nOLLAMA_API_KEY=o-key\n", mode: int = 0o600) -> Path:
    path = tmp_path / "web.env"
    path.write_text(text)
    os.chmod(path, mode)
    return path


class Clock:
    """Monotonic time the test moves; sleeping moves it too (and is recorded)."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 2))
        self.now += seconds


def web(
    tmp_path: Path,
    http: FakeHttp,
    store: Store,
    *,
    cap: int = 2000,
    month: str = "2026-10",
    text: str = "",
    clock: Clock | None = None,
    fetch: Callable[[str], Awaitable[Page]] | None = None,
    limits: Limits | None = None,
    status: PartStatus | None = None,
) -> WebSearch:
    path = keys(tmp_path, text) if text else keys(tmp_path)
    clock = clock or Clock()
    return WebSearch(
        http,
        path,
        store,
        country="NL",
        brave_cap=cap,
        month=lambda: month,
        day=lambda: "2026-10-10",
        clock=clock,
        sleep=clock.sleep,
        fetch=fetch,
        limits=limits or Limits(),
        status=status,
    )


def asked(http: FakeHttp) -> list[str]:
    """The providers asked, in order (Brave's address without its query)."""
    return [url.split("?")[0] for url in http.urls()]


def ollama_down(http: FakeHttp) -> None:
    http.add(OLLAMA_URL, 503, None)


async def test_ollama_is_asked_first(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    found = await web(tmp_path, http, store).search("  ajax   psv score ")
    assert found == WebResults("Ollama", (AJAX,)) and asked(http) == [OLLAMA_URL]
    headers, body = http.sent[0]
    assert headers["Authorization"] == "Bearer o-key"
    assert json.loads(body or b"{}") == {"query": "ajax psv score", "max_results": 5}


async def test_brave_web_search_is_the_backup(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    found = await web(tmp_path, http, store).search("ajax psv score")
    assert found.provider == "Brave"
    assert found.results[0] == WebResult(
        title="Ajax wint van PSV",
        url="https://nos.nl/artikel/1",
        site="nos.nl",
        date="2026-10-09",
        text="Ajax won 2-1 on Sunday. … The winner came late.",
    )
    assert found.results[1] == WebResult(
        "Ajax 2-1 PSV", "https://www.espn.com/soccer/2", "espn.com", "", "Match report."
    )
    method, url, _ = http.requests[-1]
    headers, body = http.sent[-1]
    assert method == "GET" and body is None and headers["X-Subscription-Token"] == "b-key"
    sent = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert sent == {
        "q": "ajax psv score",
        "count": "5",
        "country": "NL",
        "search_lang": "en",
        "safesearch": "moderate",
        "extra_snippets": "true",
        "text_decorations": "false",
    }


async def test_brave_searches_are_counted_per_month(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store)
    await search.search("one")
    await search.search("two")
    assert search.usage() == "Ollama 2 today, Brave backup 2/2000 this month"
    assert web(tmp_path, http, store, month="2026-11").usage() == "Ollama 2 today, Brave backup 0/2000 this month"


async def test_at_the_cap_brave_is_not_asked(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store, cap=1)
    assert (await search.search("one")).provider == "Brave"
    with pytest.raises(WebSearchError, match="not available right now"):
        await search.search("two")
    assert asked(http) == [OLLAMA_URL, BRAVE_URL, OLLAMA_URL]


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout", "no results", "not json"])
async def test_ollama_failures_fall_back_to_brave(tmp_path: Path, store: Store, failure: object) -> None:
    http = FakeHttp()
    if failure == "timeout":
        http.fail(OLLAMA_URL, TimeoutError())
    elif failure == "no results":
        http.add(OLLAMA_URL, 200, {"results": []})
    elif failure == "not json":
        http.add(OLLAMA_URL, 200, None)
    else:
        http.add(OLLAMA_URL, int(str(failure)), {"error": "nope"})
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    found = await web(tmp_path, http, store).search("ajax")
    assert found.provider == "Brave" and asked(http) == [OLLAMA_URL, BRAVE_URL]


@pytest.mark.parametrize(
    "error", [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), ValueError("odd"), KeyError("x")]
)
async def test_any_ollama_error_still_tries_brave(tmp_path: Path, store: Store, error: Exception) -> None:
    http = FakeHttp()
    http.fail(OLLAMA_URL, error)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    assert (await web(tmp_path, http, store).search("ajax")).provider == "Brave"


async def test_without_an_ollama_key_only_brave_is_asked(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store, text="BRAVE_API_KEY=b-key\n")
    assert (await search.search("ajax")).provider == "Brave" and asked(http) == [BRAVE_URL]
    assert search.usage() == "Brave 1/2000 this month (no Ollama key)"


async def test_without_a_brave_key_only_ollama_is_asked(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    ollama_down(http)
    search = web(tmp_path, http, store, text="OLLAMA_API_KEY=o-key\n")
    with pytest.raises(WebSearchError):
        await search.search("ajax")
    assert asked(http) == [OLLAMA_URL] and search.usage() == "Ollama 1 today (no Brave key)"


async def test_both_failing_is_an_error(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.fail(OLLAMA_URL, OSError("down"))
    http.add(BRAVE_URL, 429, {"error": {"code": "RATE_LIMITED"}})
    with pytest.raises(WebSearchError, match="not available right now"):
        await web(tmp_path, http, store).search("ajax")


async def test_nothing_found_is_an_empty_answer_not_an_outage(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, {"results": []})
    http.add(BRAVE_URL, 200, {"type": "search", "query": {"original": "zzqx"}})  # Brave leaves out "web"
    assert await web(tmp_path, http, store).search("zzqx") == WebResults("Brave", ())
    only_ollama = FakeHttp()
    only_ollama.add(OLLAMA_URL, 200, {"results": []})
    found = await web(tmp_path, only_ollama, store, text="OLLAMA_API_KEY=o-key\n").search("zzqx")
    assert found == WebResults("Ollama", ())


@pytest.mark.parametrize("body", [None, [], {"type": "search", "web": {"results": "x"}}, {"web": "x"}, {"error": 1}])
async def test_an_unreadable_brave_answer_is_a_failure(tmp_path: Path, store: Store, body: object) -> None:
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, body)
    with pytest.raises(WebSearchError):
        await web(tmp_path, http, store).search("ajax")


async def test_a_key_file_others_can_read_is_not_used(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    search = WebSearch(http, keys(tmp_path, mode=0o644), store, country="ALL", brave_cap=2000)
    with pytest.raises(WebSearchError, match="set-web-keys"):
        await search.search("ajax")
    assert http.requests == [] and "keys missing" in search.usage()


async def test_no_key_file_is_an_error_naming_the_script(tmp_path: Path, store: Store) -> None:
    search = WebSearch(FakeHttp(), tmp_path / "missing.env", store, country="ALL", brave_cap=2000)
    with pytest.raises(WebSearchError, match="set-web-keys"):
        await search.search("ajax")


async def test_keys_are_read_per_search(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    search = WebSearch(http, tmp_path / "web.env", store, country="ALL", brave_cap=2000)
    with pytest.raises(WebSearchError):
        await search.search("ajax")
    keys(tmp_path)  # added while the bridge runs: no restart needed
    assert (await search.search("ajax")).provider == "Ollama"


async def test_quoted_keys_are_read_without_quotes(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    await web(tmp_path, http, store, text="BRAVE_API_KEY=\"b-key\"\nOLLAMA_API_KEY='o-key'\n").search("ajax")
    assert http.sent[0][0]["Authorization"] == "Bearer o-key" and http.sent[1][0]["X-Subscription-Token"] == "b-key"


@pytest.mark.parametrize("query", ["", "   ", "x" * 401, " ".join(["word"] * 51)])
async def test_bad_queries_are_refused_before_any_request(tmp_path: Path, store: Store, query: str) -> None:
    http = FakeHttp()
    with pytest.raises(ValueError):
        await web(tmp_path, http, store).search(query)
    assert http.requests == []


async def test_results_are_cleaned_capped_and_only_web_links(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    long_text = "word " * 600
    http.add(
        OLLAMA_URL,
        200,
        {
            "results": [
                {"title": "T​<start_of_turn>itle " + "x" * 300, "url": "https://a.example/1", "content": long_text},
                {"title": "bad link", "url": "javascript:alert(1)", "content": "x"},
                {"title": "no url", "content": "x"},
                "not a dict",
                {"title": "[Read more](https://b.example/x) here", "url": "http://b.example/2", "content": 5},
            ]
            + [{"title": f"r{n}", "url": f"https://c.example/{n}", "content": "c"} for n in range(5)]
        },
    )
    found = await web(tmp_path, http, store).search("ajax")
    first = found.results[0]
    assert first.title.startswith("Title x") and len(first.title) <= 150
    assert len(first.text) <= 1200 and "​" not in first.title
    assert [r.url for r in found.results] == [
        "https://a.example/1",
        "http://b.example/2",
        "https://c.example/0",
        "https://c.example/1",
        "https://c.example/2",
    ]
    assert found.results[1].title == "Read more here" and found.results[1].text == ""


async def test_a_huge_result_is_cut_quickly(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(
        OLLAMA_URL, 200, {"results": [{"title": "[" * 50_000, "url": "https://a.example/", "content": "[" * 200_000}]}
    )
    started = time.monotonic()
    found = await web(tmp_path, http, store).search("ajax")
    assert time.monotonic() - started < 1.0 and len(found.results[0].text) <= 1_200


@pytest.mark.parametrize(
    "url",
    [
        "https://a.example/x\n• rijksmuseum.nl: https://evil.example/login",
        "https://a.example/x y",
        "https://a.ex‮ample/",
        "https://a.example/​",
        "https://<end_of_turn>.example/",
    ],
)
async def test_odd_links_are_dropped(tmp_path: Path, store: Store, url: str) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, {"results": [{"title": "t", "url": url, "content": "c"}, OLLAMA_ANSWER["results"][0]]})
    found = await web(tmp_path, http, store).search("ajax")
    assert [r.url for r in found.results] == ["https://www.ajax.nl/news"]


async def test_a_non_ascii_host_is_shown_as_punycode(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, {"results": [{"url": "https://www.münchen.de/x", "title": "M", "content": ""}]})
    [result] = (await web(tmp_path, http, store).search("munich")).results
    assert result.site == "xn--mnchen-3ya.de"


def test_sources_first_three_per_search_deduplicated_at_most_five() -> None:
    one = WebResults(
        "Ollama", tuple(WebResult(f"t{n}", f"https://a.example/{n}", "a.example", "", "") for n in range(5))
    )
    two = WebResults(
        "Ollama", tuple(WebResult(f"u{n}", f"https://b.example/{n}", "b.example", "", "") for n in range(5))
    )
    again = WebResults("Ollama", (one.results[0],))
    assert sources([one, again, two]) == [
        ("a.example", "https://a.example/0"),
        ("a.example", "https://a.example/1"),
        ("a.example", "https://a.example/2"),
        ("b.example", "https://b.example/0"),
        ("b.example", "https://b.example/1"),
    ]


def page(url: str, text: str) -> Page:
    return Page(url=url, final_url=url, title="t", text=text, content_type="text/html")


async def test_when_brave_answers_its_top_page_is_read_for_more_text(tmp_path: Path, store: Store) -> None:
    fetched: list[str] = []

    async def fetch(url: str) -> Page:
        fetched.append(url)
        return page(url, "Menu. " * 400 + "Ajax beat PSV 2-1 on Sunday at the Johan Cruijff ArenA. " + "Footer. " * 400)

    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    found = await web(tmp_path, http, store, fetch=fetch).search("Ajax PSV score Sunday")
    assert fetched == ["https://nos.nl/artikel/1"]
    first = found.results[0]
    assert first.text.startswith("Ajax won 2-1 on Sunday.") and "Johan Cruijff ArenA" in first.text
    assert len(first.text) <= 1_200 + 2_000 and found.results[1].text == "Match report."


async def test_a_page_that_cannot_be_read_keeps_the_blurb(tmp_path: Path, store: Store) -> None:
    async def fetch(url: str) -> Page:
        raise FetchError("the site answered HTTP 403")

    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    found = await web(tmp_path, http, store, fetch=fetch).search("ajax")
    assert found.results[0].text == "Ajax won 2-1 on Sunday. … The winner came late."


async def test_on_a_call_no_page_is_read_and_three_results_are_enough(tmp_path: Path, store: Store) -> None:
    async def fetch(url: str) -> Page:
        raise AssertionError("no page on a call")

    http = FakeHttp()
    http.add(
        OLLAMA_URL,
        200,
        {"results": [{"title": f"r{n}", "url": f"https://c.example/{n}", "content": "c"} for n in range(5)]},
    )
    found = await web(tmp_path, http, store, fetch=fetch).search("ajax", quick=True)
    assert len(found.results) == 3
    assert json.loads(http.sent[0][1] or b"{}")["max_results"] == 3


async def test_the_part_of_a_long_page_that_answers_is_kept(tmp_path: Path, store: Store) -> None:
    content = "Welcome to the museum. " * 120 + "Opening hours: every day from 9am to 5pm. " + "Shop and cafe. " * 120
    http = FakeHttp()
    http.add(
        OLLAMA_URL,
        200,
        {"results": [{"title": "Rijksmuseum", "url": "https://www.rijksmuseum.nl/en", "content": content}]},
    )
    [result] = (await web(tmp_path, http, store).search("Rijksmuseum opening hours")).results
    assert "9am to 5pm" in result.text and len(result.text) <= 1_200


async def test_every_search_asks_again(tmp_path: Path, store: Store) -> None:
    # no cache, not even in memory: Brave's terms allow only transient use of results
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    search = web(tmp_path, http, store)
    await search.search("Ajax PSV")
    await search.search("ajax psv")
    assert len(http.requests) == 2


async def test_too_many_requests_pauses_ollama_for_ten_minutes(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    http.add(OLLAMA_URL, 429, None)
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store, clock=clock)
    assert (await search.search("one")).provider == "Brave"
    clock.now += 2
    assert (await search.search("two")).provider == "Brave"  # Ollama paused: not asked
    assert "Ollama paused 10 min (too many requests)" in search.usage()
    clock.now += 600
    assert (await search.search("three")).provider == "Ollama"
    assert asked(http) == [OLLAMA_URL, BRAVE_URL, BRAVE_URL, OLLAMA_URL]


@pytest.mark.parametrize("status", [401, 402, 403])
async def test_a_refused_key_pauses_that_service_for_an_hour(tmp_path: Path, store: Store, status: int) -> None:
    clock = Clock()
    http = FakeHttp()
    http.add(OLLAMA_URL, status, None)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store, clock=clock)
    await search.search("one")
    clock.now += 30 * 60
    await search.search("two")
    assert asked(http) == [OLLAMA_URL, BRAVE_URL, BRAVE_URL]
    assert f"Ollama paused 30 min (key refused, HTTP {status})" in search.usage()
    clock.now += 30 * 60  # an hour after the refusal it is asked again
    await search.search("three")
    assert asked(http)[-2:] == [OLLAMA_URL, BRAVE_URL]


async def test_brave_once_a_second_waits_and_retries_once(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    ollama_down(http)
    per_second = {"x-ratelimit-remaining": "0, 1990", "x-ratelimit-reset": "1, 1800000"}
    http.add(BRAVE_URL, 429, {"error": {"code": "RATE_LIMITED"}}, per_second)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store, clock=clock)
    assert (await search.search("one")).provider == "Brave"
    assert clock.slept == [1.1] and asked(http) == [OLLAMA_URL, BRAVE_URL, BRAVE_URL]
    clock.now += 0.3  # a second search right away waits for Brave's one-per-second limit
    await search.search("two")
    assert clock.slept[-1] == 0.8


async def test_brave_out_of_searches_for_the_month_is_paused(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    ollama_down(http)
    month_gone = {"x-ratelimit-remaining": "1, 0", "x-ratelimit-reset": "1, 3600"}
    http.add(BRAVE_URL, 429, {"error": {"code": "RATE_LIMITED"}}, month_gone)
    search = web(tmp_path, http, store, clock=clock)
    with pytest.raises(WebSearchError):
        await search.search("one")
    clock.now += 1_800
    with pytest.raises(WebSearchError):
        await search.search("two")
    assert asked(http) == [OLLAMA_URL, BRAVE_URL, OLLAMA_URL] and clock.slept == []
    assert "Brave paused 30 min (no searches left this month)" in search.usage()


async def test_slow_services_give_up_sooner_on_a_call(tmp_path: Path, store: Store) -> None:
    class Slow(FakeHttp):
        async def __call__(
            self, method: str, url: str, headers: dict[str, str], body: bytes | None, *, socket_timeout: float = 30.0
        ) -> HttpResult:
            await asyncio.sleep(0.2)
            return await super().__call__(method, url, headers, body, socket_timeout=socket_timeout)

    http = Slow()
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    limits = Limits(ollama_s=1.0, brave_s=1.0, ollama_quick_s=0.05, brave_quick_s=0.05)
    search = web(tmp_path, http, store, limits=limits)
    assert (await search.search("ajax")).provider == "Ollama"
    with pytest.raises(WebSearchError):
        await search.search("ajax psv", quick=True)


async def test_on_a_call_brave_is_not_retried_after_its_one_a_second_limit(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    ollama_down(http)
    per_second = {"x-ratelimit-remaining": "0, 1990", "x-ratelimit-reset": "1, 1800000"}
    http.add(BRAVE_URL, 429, {"error": {"code": "RATE_LIMITED"}}, per_second)
    with pytest.raises(WebSearchError):
        await web(tmp_path, http, store, clock=clock).search("ajax", quick=True)
    assert clock.slept == [] and asked(http) == [OLLAMA_URL, BRAVE_URL]


@pytest.mark.parametrize(
    "query",
    [
        "who is anna.smith@example.com",
        "bank NL91ABNA0417164300 owner",
        "bank NL91 ABNA 0417 1643 00 owner",
        "who called me from +31 6 1234 5678",
        "account 123456789",
    ],
)
async def test_private_details_never_go_into_a_search(tmp_path: Path, store: Store, query: str) -> None:
    http = FakeHttp()
    with pytest.raises(ValueError, match="private details never go to a search engine"):
        await web(tmp_path, http, store).search(query)
    assert http.requests == []


@pytest.mark.parametrize("query", ["Ajax PSV 1-3 2026", "news 2026-10-10", "population of the Netherlands 17900000"])
async def test_dates_scores_and_ordinary_numbers_are_fine(tmp_path: Path, store: Store, query: str) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 200, OLLAMA_ANSWER)
    assert (await web(tmp_path, http, store).search(query)).provider == "Ollama"


async def test_each_request_gets_its_own_time_limit(tmp_path: Path, store: Store) -> None:
    # the request itself stops at the limit (not only the wait for it), so a late Brave request cannot still count
    http = FakeHttp()
    ollama_down(http)
    http.add(BRAVE_URL, 200, BRAVE_ANSWER)
    search = web(tmp_path, http, store)
    await search.search("ajax")
    await search.search("ajax psv", quick=True)
    assert http.timeouts == [8.0, 5.0, 5.0, 3.0]


NOW = 1_791_452_400.0


def recorder(tmp_path: Path) -> PartStatus:
    return PartStatus(tmp_path / "status.json", clock=lambda: NOW)


async def test_a_search_that_got_an_answer_is_recorded(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(OLLAMA_URL, 429, None)
    http.add(BRAVE_URL, 200, {"type": "search", "query": {"original": "zzqx"}})  # nothing found is an answer
    status = recorder(tmp_path)
    await web(tmp_path, http, store, status=status).search("zzqx")
    assert status.snapshot() == {"web": Record(ok=NOW)}  # the backup answered: web search works


@pytest.mark.parametrize(
    ("ollama", "brave", "record"),
    [
        (OSError("down"), (429, {"error": {"code": "RATE_LIMITED"}}), Record(failed=NOW, reason="busy", code=429)),
        ((401, None), None, Record(failed=NOW, reason="refused", code=401)),
        (TimeoutError(), None, Record(failed=NOW, reason="no_answer")),
        ((200, None), None, Record(failed=NOW, reason="bad_answer")),
    ],
)
async def test_a_search_no_service_answered_is_recorded_with_the_last_reason(
    tmp_path: Path, store: Store, ollama: object, brave: object, record: Record
) -> None:
    http = FakeHttp()
    if isinstance(ollama, BaseException):
        http.fail(OLLAMA_URL, ollama)
    else:
        assert isinstance(ollama, tuple)
        http.add(OLLAMA_URL, *ollama)
    if isinstance(brave, tuple):
        http.add(BRAVE_URL, *brave)
    status = recorder(tmp_path)
    text = "BRAVE_API_KEY=b-key\nOLLAMA_API_KEY=o-key\n" if brave else "OLLAMA_API_KEY=o-key\n"
    with pytest.raises(WebSearchError):
        await web(tmp_path, http, store, text=text, status=status).search("ajax")
    assert status.snapshot() == {"web": record}


async def test_missing_keys_are_recorded_and_bad_queries_are_not(tmp_path: Path, store: Store) -> None:
    status = recorder(tmp_path)
    search = WebSearch(FakeHttp(), tmp_path / "missing.env", store, country="ALL", brave_cap=2000, status=status)
    with pytest.raises(ValueError):
        await search.search("mail alex@example.com about it")
    assert status.snapshot() == {}
    with pytest.raises(WebSearchError):
        await search.search("ajax")
    assert status.snapshot() == {"web": Record(failed=NOW, reason="no_key")}
    assert "ajax" not in (tmp_path / "status.json").read_text()


async def test_a_search_with_every_service_paused_records_nothing_new(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    http.add(OLLAMA_URL, 429, None)
    status = recorder(tmp_path)
    search = web(tmp_path, http, store, clock=clock, text="OLLAMA_API_KEY=o-key\n", status=status)
    with pytest.raises(WebSearchError):
        await search.search("one")
    with pytest.raises(WebSearchError):
        await search.search("two")  # Ollama paused: nothing asked, the first failure stands
    assert status.snapshot() == {"web": Record(failed=NOW, reason="busy", code=429)} and len(http.requests) == 1
