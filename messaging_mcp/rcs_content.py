"""Builds and checks RCS message content: text, a card, or a carousel.

The tools take a small, flat description of the message. This module turns
it into the shape Infobip's RCS API expects and refuses what RCS would
reject, so mistakes show up in the proposal and not after sending.
"""

from typing import Any

from fastmcp.exceptions import ToolError

MAX_TEXT = 3072
MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MAX_BUTTON_TEXT = 25
MAX_BUTTONS_PER_MESSAGE = 11
MAX_BUTTONS_PER_CARD = 4
MIN_CARDS = 2
MAX_CARDS = 10
_HEIGHTS = ("SHORT", "MEDIUM", "TALL")
_BUTTON_TYPES = ("REPLY", "OPEN_URL", "DIAL_PHONE")


def _text(value: Any, what: str, limit: int, required: bool = True) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        if required:
            raise ToolError(f"{what} is required.")
        return None
    if len(text) > limit:
        raise ToolError(f"{what} can be at most {limit} characters, got {len(text)}.")
    return text


def _url(value: Any, what: str) -> str:
    url = _text(value, what, 2048)
    if not str(url).lower().startswith("https://"):
        raise ToolError(f"{what} must start with https://.")
    return str(url)


def _buttons(items: Any, limit: int, where: str) -> list[dict[str, Any]]:
    items = items or []
    if not isinstance(items, list):
        raise ToolError(f"The buttons of {where} must be a list.")
    if len(items) > limit:
        raise ToolError(f"{where} can have at most {limit} buttons.")
    built = []
    for item in items:
        kind = str(item.get("type") or "").strip().upper()
        if kind not in _BUTTON_TYPES:
            raise ToolError("A button type must be REPLY, OPEN_URL or DIAL_PHONE.")
        text = _text(item.get("text"), "Button text", MAX_BUTTON_TEXT)
        button: dict[str, Any] = {
            "type": kind,
            "text": text,
            "postbackData": str(item.get("postback_data") or text),
        }
        if kind == "OPEN_URL":
            button["url"] = _url(item.get("url"), "Button url")
        if kind == "DIAL_PHONE":
            number = str(item.get("phone_number") or "").strip()
            if not number.lstrip("+").isdigit():
                raise ToolError("A DIAL_PHONE button needs phone_number in digits.")
            button["phoneNumber"] = number
        built.append(button)
    return built


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
    if card.get("image_url"):
        height = str(card.get("image_height") or "MEDIUM").upper()
        if height not in _HEIGHTS:
            raise ToolError("image_height must be SHORT, MEDIUM or TALL.")
        built["media"] = {
            "file": {"url": _url(card["image_url"], f"The image of {where}")},
            "height": height,
        }
    if not (title or description or "media" in built):
        raise ToolError(f"{where} needs a title, a description or an image.")
    buttons = _buttons(card.get("buttons"), MAX_BUTTONS_PER_CARD, where)
    if buttons:
        built["suggestions"] = buttons
    return built


def build(content: dict[str, Any]) -> dict[str, Any]:
    """Returns the API content for a flat description, or raises ToolError."""
    if not isinstance(content, dict):
        raise ToolError("content must be an object with a 'type'.")
    kind = str(content.get("type") or "").strip().upper()
    if kind == "TEXT":
        built: dict[str, Any] = {
            "type": "TEXT",
            "text": _text(content.get("text"), "text", MAX_TEXT),
        }
    elif kind == "CARD":
        orientation = str(content.get("orientation") or "VERTICAL").upper()
        if orientation not in ("VERTICAL", "HORIZONTAL"):
            raise ToolError("orientation must be VERTICAL or HORIZONTAL.")
        built = {
            "type": "CARD",
            "orientation": orientation,
            "alignment": "LEFT",
            "content": _card(content, "the card"),
        }
        # Buttons given on a card belong to the card itself.
        return built
    elif kind == "CAROUSEL":
        cards = content.get("cards") or []
        if not MIN_CARDS <= len(cards) <= MAX_CARDS:
            raise ToolError(
                f"A carousel has between {MIN_CARDS} and {MAX_CARDS} cards."
            )
        width = str(content.get("card_width") or "MEDIUM").upper()
        if width not in ("SMALL", "MEDIUM"):
            raise ToolError("card_width must be SMALL or MEDIUM.")
        built = {
            "type": "CAROUSEL",
            "cardWidth": width,
            "contents": [
                _card(card, f"card {index + 1}") for index, card in enumerate(cards)
            ],
        }
    else:
        raise ToolError("content type must be TEXT, CARD or CAROUSEL.")
    buttons = _buttons(content.get("buttons"), MAX_BUTTONS_PER_MESSAGE, "the message")
    if buttons:
        built["suggestions"] = buttons
    return built


def describe(built: dict[str, Any]) -> dict[str, Any]:
    """A short, readable summary of built content for a proposal."""
    def card(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": c.get("title"),
            "description": c.get("description"),
            "image": (c.get("media") or {}).get("file", {}).get("url"),
            "buttons": [b["text"] for b in c.get("suggestions", [])],
        }

    summary: dict[str, Any] = {"type": built["type"]}
    if built["type"] == "TEXT":
        summary["text"] = built["text"]
    elif built["type"] == "CARD":
        summary["card"] = card(built["content"])
    else:
        summary["cards"] = [card(c) for c in built["contents"]]
    if built.get("suggestions"):
        summary["buttons"] = [b["text"] for b in built["suggestions"]]
    return summary
