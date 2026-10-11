"""A pretend web search for the tool tests and the Gemma test set: canned results per query word, or a failure."""

from __future__ import annotations

from local_assistant.web_search import WebResult, WebResults, WebSearchError


class FakeWeb:
    def __init__(self, results: dict[str, list[WebResult]] | None = None) -> None:
        self.results = results or {}  # a word in the query → its results
        self.queries: list[str] = []
        self.quick: list[bool] = []  # per search: on a call
        self.down = False

    async def search(self, query: str, *, quick: bool = False) -> WebResults:
        self.queries.append(query)
        self.quick.append(quick)
        if self.down:
            raise WebSearchError("web search is not available right now")
        for word, found in self.results.items():
            if word.lower() in query.lower():
                return WebResults("Brave", tuple(found))
        return WebResults("Brave", ())
