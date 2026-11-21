"""Reads recipients from People: everyone with a tag, or everyone in a segment."""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from fastmcp.exceptions import ToolError

from messaging_mcp import api

MAX_RECIPIENTS_ENV_VAR = "MESSAGING_MCP_MAX_RECIPIENTS"
DEFAULT_MAX_RECIPIENTS = 20000
_PAGE_SIZE = 1000
AUDIENCE_KINDS = ("tag", "segment")
CHANNELS = ("EMAIL", "SMS", "RCS")


def max_recipients() -> int:
    raw = os.environ.get(MAX_RECIPIENTS_ENV_VAR, str(DEFAULT_MAX_RECIPIENTS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ToolError(f"{MAX_RECIPIENTS_ENV_VAR} must be a number.") from exc
    if value < 1:
        raise ToolError(f"{MAX_RECIPIENTS_ENV_VAR} must be at least 1.")
    return value


@dataclass
class Recipient:
    address: str
    first_name: str | None = None
    last_name: str | None = None


@dataclass
class Audience:
    kind: str
    value: str
    label: str
    channel: str
    recipients: list[Recipient] = field(default_factory=list)
    people_total: int = 0
    without_address: int = 0
    duplicates: int = 0

    def fingerprint(self) -> str:
        """Changes when anyone joins or leaves the audience."""
        joined = json.dumps(sorted(r.address for r in self.recipients))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "audience": f"{self.kind}: {self.label}",
            "people_in_audience": self.people_total,
            "recipients": len(self.recipients),
            "skipped_without_address": self.without_address,
            "skipped_duplicates": self.duplicates,
            "first_recipients": [r.address for r in self.recipients[:5]],
        }


def _people_in(answer: Any) -> list[dict[str, Any]]:
    if isinstance(answer, list):
        return answer
    for key in ("persons", "content", "results"):
        if isinstance((answer or {}).get(key), list):
            return answer[key]
    return []


def _address(person: dict[str, Any], channel: str) -> str | None:
    contact = person.get("contactInformation") or {}
    if channel == "EMAIL":
        for entry in contact.get("email") or []:
            if entry.get("address"):
                return str(entry["address"]).strip().lower()
        return None
    for entry in contact.get("phone") or []:
        if entry.get("number"):
            return str(entry["number"]).strip().lstrip("+")
    return None


def _page(account: str, kind: str, value: str, page: int) -> list[dict[str, Any]]:
    if kind == "tag":
        answer = api.request(
            account,
            "GET",
            "/people/2/persons",
            params={
                "filter": json.dumps({"#contains": {"tags": value}}),
                "limit": _PAGE_SIZE,
                "page": page + 1,  # This endpoint counts pages from 1.
            },
        )
    else:
        answer = api.request(
            account,
            "GET",
            f"/people/3/segments/{quote(str(value), safe='')}/persons",
            params={"size": _PAGE_SIZE, "page": page},
        )
    return _people_in(answer)


def _label(account: str, kind: str, value: str) -> str:
    """Looks the tag or segment up, so a wrong name fails before any send."""
    if kind == "tag":
        tag = api.request(
            account, "GET", f"/people/2/tags/{quote(value, safe='')}"
        )
        return str((tag or {}).get("name") or value)
    segment = api.request(
        account, "GET", f"/people/3/segments/{quote(str(value), safe='')}"
    )
    return str((segment or {}).get("name") or value)


def load_audience(account: str, kind: str, value: str, channel: str) -> Audience:
    kind = (kind or "").strip().lower()
    channel = (channel or "").strip().upper()
    value = str(value or "").strip()
    if kind not in AUDIENCE_KINDS:
        raise ToolError("audience_kind must be 'tag' or 'segment'.")
    if channel not in CHANNELS:
        raise ToolError("channel must be EMAIL, SMS or RCS.")
    if not value:
        raise ToolError("audience_value is required: a tag name or a segment ID.")
    audience = Audience(
        kind=kind, value=value, label=_label(account, kind, value), channel=channel
    )
    limit = max_recipients()
    seen: set[str] = set()
    page = 0
    while True:
        people = _page(account, kind, value, page)
        for person in people:
            audience.people_total += 1
            address = _address(person, channel)
            if not address:
                audience.without_address += 1
            elif address in seen:
                audience.duplicates += 1
            else:
                seen.add(address)
                audience.recipients.append(
                    Recipient(
                        address=address,
                        first_name=person.get("firstName"),
                        last_name=person.get("lastName"),
                    )
                )
        if len(audience.recipients) > limit:
            raise ToolError(
                f"The audience has more than {limit} recipients, which is the "
                f"limit per send ({MAX_RECIPIENTS_ENV_VAR}). Nothing was sent."
            )
        if len(people) < _PAGE_SIZE:
            return audience
        page += 1
