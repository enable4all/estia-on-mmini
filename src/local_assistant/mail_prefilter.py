"""Mail pre-filter: bulk mail recognised from its headers (no model), then AgentJev (a small local decision model on
the CPU) for "needs nothing from the user", before Gemma sorts the rest. The whole funnel: AGENTS.md, "Mail sorting
funnel".

Jev answers with a probability, never text, so nothing a mail says can reach Gemma or a tool through it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

JEV_TIMEOUT = 8.0  # its first answer after idling can take a few seconds (sorting runs in the background)
STATE_CHARS = 1_500  # Jev reads at most 2,048 tokens
NOISE_QUESTION = {
    "id": "noise",
    "type": "boolean",
    "question": "Does this mail need nothing at all from the user?",
    "criteria": {
        "true": (
            "A newsletter, promotion, marketing mail, social media notice, receipt for something already done, or an "
            "automated notification: the user needs to do nothing, reply to nothing and remember nothing."
        ),
        "false": (
            "A person writes to the user, asks something, needs a reply or an action, mentions a deadline, a bill, "
            "an appointment, a problem with an account, or anything the user should know or do."
        ),
    },
}


def _header(headers: object, name: str) -> str | None:
    if not isinstance(headers, list):
        return None
    for item in headers:
        if isinstance(item, dict) and str(item.get("name", "")).casefold() == name.casefold():
            value = item.get("value")
            return value.strip() if isinstance(value, str) else ""
    return None


def header_noise(headers: object) -> tuple[str, bool] | None:
    """What the headers say about bulk or automatic mail, and whether that decides on its own: List-Id and
    Precedence bulk/list/junk do (a list or bulk mailing); List-Unsubscribe and auto-generated mail only hint (bills
    and security notices carry them too), so Jev must agree. `headers`: Graph's internetMessageHeaders."""
    if _header(headers, "List-Id") is not None:
        return "mailing list", True
    if (_header(headers, "Precedence") or "").casefold() in ("bulk", "list", "junk"):
        return "bulk mail", True
    if _header(headers, "List-Unsubscribe") is not None:
        return "newsletter", False
    submitted = _header(headers, "Auto-Submitted")
    if (submitted is not None and submitted.casefold() != "no") or any(
        _header(headers, name) is not None for name in ("X-Autoreply", "X-Autorespond")
    ):
        return "auto-reply", False
    return None


def is_bounce(headers: object) -> bool:
    """A delivery failure report (often of the user's own mail): never noise."""
    content_type = (_header(headers, "Content-Type") or "").casefold()
    return _header(headers, "X-MS-Exchange-Message-Is-Ndr") is not None or "report-type=delivery-status" in content_type


_ATTENTION = re.compile(
    # English as whole words; Dutch stems anywhere in a word (Dutch glues words together: betalingsherinnering)
    r"\b(?:invoices?|payments?|pay|paid|overdue|unpaid|declined|failed|returned|refunds?|bills?|debit|charged?|"
    r"security|sign[- ]?in|log[- ]?in|password|verify|verification|suspended|locked|deadline|due|appointments?)\b"
    r"|factuur|facturen|betaling|betaal|aanmaning|herinnering|incasso|wachtwoord|inlog|ingelogd|beveilig|"
    r"afspra(?:ak|ken)|vervaldatum",
    re.IGNORECASE,
)


def needs_attention(subject: str, body: str) -> bool:
    """Words of bills, payments, security and appointments (English and Dutch): such mail always reaches Gemma."""
    return _ATTENTION.search(f"{subject}\n{body[:STATE_CHARS]}") is not None


class Jev:
    """The local AgentJev service (127.0.0.1): one yes/no question per mail, with criteria (its strongest mode)."""

    def __init__(self, url: str, *, timeout: float = JEV_TIMEOUT) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout

    async def noise_probability(self, sender: str, subject: str, body: str) -> float | None:
        """How sure Jev is that the mail needs nothing from the user (0-1); None when Jev cannot answer."""
        state = f"From: {sender}\nSubject: {subject}\n\n{body}"[:STATE_CHARS]
        payload = json.dumps({"state": state, "questions": [NOISE_QUESTION]}).encode()
        try:
            data = await asyncio.wait_for(asyncio.to_thread(self._post, payload), self._timeout + 1)
            answer: dict[str, Any] = data["results"][0]["answers"][0]
            p_yes = float(answer["probability"])  # Jev's probability of "yes", whichever answer it chose
        except Exception as exc:  # down, slow, an odd answer: the mail simply goes to Gemma
            log.info("mail pre-filter: Jev did not answer (%s)", type(exc).__name__)
            return None
        return min(max(p_yes, 0.0), 1.0)

    def _post(self, payload: bytes) -> Any:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"{self._url}/api/evaluate", data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with opener.open(request, timeout=self._timeout) as response:
            return json.loads(response.read(1_000_000))
