"""Scripted HTTP for the mail tests: answers per URL in order (the last one repeats), every request recorded."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from local_assistant.mail_http import HttpResult


class FakeHttp:
    def __init__(self) -> None:
        self.answers: dict[str, list[HttpResult | BaseException]] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.sent: list[tuple[dict[str, str], bytes | None]] = []  # raw headers and body, per request
        self.timeouts: list[float] = []  # the time limit each request was given

    def add(self, url: str, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self.answers.setdefault(url, []).append(HttpResult(status, headers or {}, body))

    def fail(self, url: str, error: BaseException) -> None:
        self.answers.setdefault(url, []).append(error)

    async def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, *, socket_timeout: float = 30.0
    ) -> HttpResult:
        self.timeouts.append(socket_timeout)
        self.sent.append((dict(headers), body))
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()} if body else {}
        self.requests.append(
            (
                method,
                url,
                form | {"authorization": headers.get("Authorization", ""), "prefer": headers.get("Prefer", "")},
            )
        )
        answers = self.answers.get(url) or self.answers.get(url.split("?")[0])  # a GET's query is optional
        if not answers:
            raise AssertionError(f"unexpected request {method} {url}")
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def urls(self) -> list[str]:
        return [url for _, url, _ in self.requests]
