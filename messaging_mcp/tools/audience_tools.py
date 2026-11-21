"""Shows which tags and segments exist and who a send would reach."""

from typing import Any

from messaging_mcp import api
from messaging_mcp.app import mcp
from messaging_mcp.audience import load_audience


@mcp.tool
def audience_list_tags(account: str) -> dict[str, Any]:
    """Lists the tags in People. A tag is how People groups a fixed list.

    Args:
        account: Account alias from accounts_list.
    """
    answer = api.request(
        account, "GET", "/people/2/tags", params={"limit": 1000, "page": 1}
    )
    tags = answer.get("tags", []) if isinstance(answer, dict) else answer or []
    return {
        "tags": [
            {"name": tag.get("name"), "description": tag.get("description")}
            for tag in tags
        ]
    }


@mcp.tool
def audience_list_segments(account: str) -> dict[str, Any]:
    """Lists the segments in People with the ID that the send tools need.

    Args:
        account: Account alias from accounts_list.
    """
    answer = api.request(
        account, "GET", "/people/3/segments", params={"size": 1000, "page": 0}
    )
    if isinstance(answer, dict):
        segments = answer.get("results") or answer.get("content") or []
    else:
        segments = answer or []
    return {
        "segments": [
            {
                "segment_id": segment.get("id"),
                "name": segment.get("name"),
                "description": segment.get("description"),
            }
            for segment in segments
        ]
    }


@mcp.tool
def audience_preview(
    account: str, audience_kind: str, audience_value: str, channel: str
) -> dict[str, Any]:
    """Counts who a send to a tag or segment would reach. Sends nothing.

    Args:
        account: Account alias from accounts_list.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID from
            audience_list_segments.
        channel: EMAIL, SMS or RCS. Decides whether email addresses or
            phone numbers are counted.
    """
    return load_audience(account, audience_kind, audience_value, channel).summary()
