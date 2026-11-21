"""Builds and checks RCS content: text, file, card, carousel or operator template.

The tools take a small, flat description of the message. This module turns
it into the shape Infobip's RCS API expects and refuses what RCS would
reject, so mistakes show up in the proposal and not after sending.

Texts may contain {{firstName}} and {{lastName}}, optionally with a default
for people who lack the value: {{firstName|kund}}. They are filled in per
recipient by `render`, and the result is checked again by `build`.
"""

import copy
import datetime
import re
from typing import Any

from fastmcp.exceptions import ToolError

MAX_TEXT = 3072
MAX_TEMPLATE_TEXT = 2500
MAX_PDF_TEXT = 1000
MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MAX_BUTTON_TEXT = 25
MAX_POSTBACK = 2024
MAX_LABEL = 100
MAX_EVENT_TITLE = 100
MAX_EVENT_DESCRIPTION = 500
MAX_TEMPLATE_NAME = 20
MAX_BUTTONS_PER_MESSAGE = 11
MAX_BUTTONS_PER_CARD = 4
MIN_CARDS = 2
MAX_CARDS = 10
_HEIGHTS = ("SHORT", "MEDIUM", "TALL")
_BUTTON_TYPES = (
    "REPLY",
    "OPEN_URL",
    "DIAL_PHONE",
    "SHOW_LOCATION",
    "REQUEST_LOCATION",
    "CREATE_CALENDAR_EVENT",
)
PLACEHOLDER_NAMES = ("firstName", "lastName")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z]+)\s*(?:\|([^}]*))?\}\}")


def _text(value: Any, what: str, limit: int, required: bool = True) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        if required:
            raise ToolError(f"{what} is required.")
        return None
    if len(text) > limit:
        raise ToolError(f"{what} can be at most {limit} characters, got {len(text)}.")
    return text


def _url(value: Any, what: str, limit: int = 2048) -> str:
    url = str(_text(value, what, limit))
    if not url.lower().startswith("https://"):
        raise ToolError(f"{what} must start with https://.")
    return url


def _moment(value: Any, what: str) -> str:
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ToolError(
            f"{what} must be a date and time such as 2026-12-01T18:00:00+01:00."
        ) from exc
    if parsed.tzinfo is None:
        raise ToolError(f"{what} needs a time zone, e.g. +01:00.")
    return parsed.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coordinate(value: Any, what: str, limit: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{what} must be a number.") from exc
    if not -limit <= number <= limit:
        raise ToolError(f"{what} must be between -{limit:g} and {limit:g}.")
    return number


def _button(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("type") or "").strip().upper()
    if kind not in _BUTTON_TYPES:
        raise ToolError("A button type must be one of " + ", ".join(_BUTTON_TYPES) + ".")
    text = str(_text(item.get("text"), "Button text", MAX_BUTTON_TEXT))
    button: dict[str, Any] = {
        "type": kind,
        "text": text,
        "postbackData": str(
            _text(item.get("postback_data") or text, "postback_data", MAX_POSTBACK)
        ),
    }
    if kind == "OPEN_URL":
        button["url"] = _url(item.get("url"), "Button url")
        open_in = str(item.get("open_in") or "BROWSER").upper()
        if open_in not in ("BROWSER", "WEBVIEW"):
            raise ToolError("open_in must be BROWSER or WEBVIEW.")
        button["application"] = open_in
        if open_in == "WEBVIEW":
            size = str(item.get("webview_size") or "FULL").upper()
            if size not in ("FULL", "HALF", "TALL"):
                raise ToolError("webview_size must be FULL, HALF or TALL.")
            button["webviewViewMode"] = size
    elif kind == "DIAL_PHONE":
        number = str(item.get("phone_number") or "").strip()
        if not number.lstrip("+").isdigit():
            raise ToolError("A DIAL_PHONE button needs phone_number in digits.")
        button["phoneNumber"] = number
    elif kind == "SHOW_LOCATION":
        button["latitude"] = _coordinate(item.get("latitude"), "latitude", 90)
        button["longitude"] = _coordinate(item.get("longitude"), "longitude", 180)
        label = _text(item.get("label"), "Location label", MAX_LABEL, False)
        if label:
            button["label"] = label
    elif kind == "CREATE_CALENDAR_EVENT":
        start = _moment(item.get("start_time"), "start_time")
        end = _moment(item.get("end_time"), "end_time")
        if end <= start:
            raise ToolError("end_time must be after start_time.")
        button["startTime"] = start
        button["endTime"] = end
        button["title"] = _text(item.get("title"), "Event title", MAX_EVENT_TITLE)
        description = _text(
            item.get("description"), "Event description", MAX_EVENT_DESCRIPTION, False
        )
        if description:
            button["description"] = description
    return button


def _buttons(items: Any, limit: int, where: str) -> list[dict[str, Any]]:
    items = items or []
    if not isinstance(items, list):
        raise ToolError(f"The buttons of {where} must be a list.")
    if len(items) > limit:
        raise ToolError(f"{where} can have at most {limit} buttons.")
    return [_button(item) for item in items]


def _media(source: dict[str, Any], where: str) -> dict[str, Any] | None:
    if not source.get("image_url"):
        return None
    height = str(source.get("image_height") or "MEDIUM").upper()
    if height not in _HEIGHTS:
        raise ToolError("image_height must be SHORT, MEDIUM or TALL.")
    media: dict[str, Any] = {
        "file": {"url": _url(source["image_url"], f"The image of {where}")},
        "height": height,
    }
    if source.get("thumbnail_url"):
        media["thumbnail"] = {
            "url": _url(source["thumbnail_url"], f"The thumbnail of {where}")
        }
    return media


def _card(card: dict[str, Any], where: str) -> dict[str, Any]:
    built: dict[str, Any] = {}
    title = _text(card.get("title"), f"The title of {where}", MAX_TITLE, False)
    description = _text(
        card.get("description"), f"The description of {where}", MAX_DESCRIPTION, False
    )
    if title:
        built["title"] = title
    if description:
        built["description"] = description
    media = _media(card, where)
    if media:
        built["media"] = media
    if not built:
        raise ToolError(f"{where} needs a title, a description or an image.")
    buttons = _buttons(card.get("buttons"), MAX_BUTTONS_PER_CARD, where)
    if buttons:
        built["suggestions"] = buttons
    return built


def _card_message(content: dict[str, Any]) -> dict[str, Any]:
    orientation = str(content.get("orientation") or "VERTICAL").upper()
    if orientation not in ("VERTICAL", "HORIZONTAL"):
        raise ToolError("orientation must be VERTICAL or HORIZONTAL.")
    alignment = str(content.get("image_side") or "LEFT").upper()
    if alignment not in ("LEFT", "RIGHT"):
        raise ToolError("image_side must be LEFT or RIGHT.")
    return {
        "type": "CARD",
        "orientation": orientation,
        "alignment": alignment,
        "content": _card(content, "the card"),
    }


def _carousel(content: dict[str, Any]) -> dict[str, Any]:
    cards = content.get("cards") or []
    if not isinstance(cards, list) or not MIN_CARDS <= len(cards) <= MAX_CARDS:
        raise ToolError(f"A carousel has between {MIN_CARDS} and {MAX_CARDS} cards.")
    width = str(content.get("card_width") or "MEDIUM").upper()
    if width not in ("SMALL", "MEDIUM"):
        raise ToolError("card_width must be SMALL or MEDIUM.")
    return {
        "type": "CAROUSEL",
        "cardWidth": width,
        "contents": [_card(card, f"card {i + 1}") for i, card in enumerate(cards)],
    }


def _kind(content: Any) -> str:
    if not isinstance(content, dict):
        raise ToolError("content must be an object with a 'type'.")
    return str(content.get("type") or "").strip().upper()


def build(content: dict[str, Any]) -> dict[str, Any]:
    """Returns the API content of a message, or raises ToolError."""
    kind = _kind(content)
    if kind == "TEXT":
        built: dict[str, Any] = {
            "type": "TEXT",
            "text": _text(content.get("text"), "text", MAX_TEXT),
        }
    elif kind == "FILE":
        built = {"type": "FILE", "file": {"url": _url(content.get("file_url"), "file_url")}}
        if content.get("thumbnail_url"):
            built["thumbnail"] = {"url": _url(content["thumbnail_url"], "thumbnail_url")}
    elif kind == "CARD":
        # Buttons given on a card belong to the card itself.
        return _card_message(content)
    elif kind == "CAROUSEL":
        built = _carousel(content)
    elif kind == "TEMPLATE":
        name = _text(content.get("name"), "Template name", MAX_TEMPLATE_NAME)
        parameters = content.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ToolError("Template parameters must be an object of name: value.")
        if content.get("buttons"):
            raise ToolError("A template carries its own buttons. Leave buttons out.")
        return {
            "type": "PROVIDER_TEMPLATE",
            "templateId": name,
            "parameters": [
                {"name": str(k), "value": str(v)} for k, v in parameters.items()
            ],
        }
    else:
        raise ToolError(
            "content type must be TEXT, FILE, CARD, CAROUSEL or TEMPLATE."
        )
    buttons = _buttons(content.get("buttons"), MAX_BUTTONS_PER_MESSAGE, "the message")
    if buttons:
        built["suggestions"] = buttons
    return built


def build_template(content: dict[str, Any]) -> dict[str, Any]:
    """Returns the content of an operator template, or raises ToolError."""
    kind = _kind(content)
    if kind == "TEXT":
        built: dict[str, Any] = {
            "type": "TEXT",
            "text": _text(content.get("text"), "text", MAX_TEMPLATE_TEXT),
        }
    elif kind == "TEXT_WITH_PDF":
        built = {
            "type": "TEXT_WITH_PDF",
            "text": _text(content.get("text"), "text", MAX_PDF_TEXT),
            "url": _url(content.get("pdf_url"), "pdf_url", MAX_PDF_TEXT),
            "messageOrder": "PDF_AT_TOP" if content.get("pdf_first") else "TEXT_MESSAGE_AT_TOP",
        }
    elif kind == "CARD":
        return _card_message(content)
    elif kind == "CAROUSEL":
        built = _carousel(content)
    else:
        raise ToolError("A template type must be TEXT, TEXT_WITH_PDF, CARD or CAROUSEL.")
    buttons = _buttons(content.get("buttons"), MAX_BUTTONS_PER_MESSAGE, "the template")
    if buttons:
        built["suggestions"] = buttons
    return built


def placeholders_in(content: Any) -> dict[str, str | None]:
    """Returns each placeholder name with its default, or None without one."""
    found: dict[str, str | None] = {}

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for name, default in _PLACEHOLDER.findall(value):
                if name not in PLACEHOLDER_NAMES:
                    raise ToolError(
                        f"Unknown placeholder {{{{{name}}}}}. Use "
                        + " or ".join("{{" + n + "}}" for n in PLACEHOLDER_NAMES)
                        + "."
                    )
                if default.strip() or name not in found:
                    found[name] = default.strip() or found.get(name)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(content)
    return found


def render(content: dict[str, Any], values: dict[str, str | None]) -> dict[str, Any]:
    """Fills in placeholders in the flat content for one recipient."""

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return str(value).strip() if value else (match.group(2) or "").strip()

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return _PLACEHOLDER.sub(fill, value)
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(copy.deepcopy(content))


def describe(built: dict[str, Any]) -> dict[str, Any]:
    """A short, readable summary of built content for a proposal."""

    def card(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": c.get("title"),
            "description": c.get("description"),
            "image": (c.get("media") or {}).get("file", {}).get("url"),
            "buttons": [_button_label(b) for b in c.get("suggestions", [])],
        }

    kind = built["type"]
    summary: dict[str, Any] = {"type": kind}
    if kind in ("TEXT", "TEXT_WITH_PDF"):
        summary["text"] = built["text"]
        if kind == "TEXT_WITH_PDF":
            summary["pdf"] = built["url"]
    elif kind == "FILE":
        summary["file"] = built["file"]["url"]
    elif kind == "CARD":
        summary["card"] = card(built["content"])
        summary["layout"] = f"{built['orientation']}, image {built['alignment']}"
    elif kind == "CAROUSEL":
        summary["cards"] = [card(c) for c in built["contents"]]
    elif kind == "PROVIDER_TEMPLATE":
        summary["template"] = built["templateId"]
        summary["parameters"] = {p["name"]: p["value"] for p in built["parameters"]}
    if built.get("suggestions"):
        summary["buttons"] = [_button_label(b) for b in built["suggestions"]]
    return summary


def _button_label(button: dict[str, Any]) -> str:
    detail = {
        "OPEN_URL": button.get("url"),
        "DIAL_PHONE": button.get("phoneNumber"),
        "SHOW_LOCATION": button.get("label")
        or f"{button.get('latitude')}, {button.get('longitude')}",
        "CREATE_CALENDAR_EVENT": f"{button.get('title')} {button.get('startTime')}",
        "REQUEST_LOCATION": "asks for the recipient's location",
    }.get(button["type"])
    return f"{button['text']} ({detail})" if detail else button["text"]
