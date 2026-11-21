"""Moments flows. The API can only add people to a flow that is already active.

Flows cannot be created, edited, launched or listed through Infobip's API.
"""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api
from messaging_mcp.app import mcp
from messaging_mcp.confirm import propose_or_apply

_MAX_PARTICIPANTS = 1000
_IDENTIFIER_TYPES = ("EMAIL", "PHONE", "EXTERNAL_ID")


@mcp.tool
def flows_add_participants(
    account: str,
    flow_id: int,
    identifiers: list[str],
    identifier_type: str,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Adds people to an active Moments flow. Two steps: propose, confirm.

    The flow then does whatever it is built to do, which usually means
    sending messages. The flow must already be active and have a Flow API
    entry point. Creating, starting and listing flows is not possible
    through the API, so the user finds the flow ID in the Infobip web
    interface (it is the number in the flow's address).

    Args:
        account: Account alias from accounts_list.
        flow_id: The flow's ID.
        identifiers: Up to 1000 email addresses, phone numbers or external IDs.
        identifier_type: EMAIL, PHONE or EXTERNAL_ID. One type per call.
        validate_only: True proposes, False adds the people.
        checksum: Checksum from the proposal. Required when adding.
    """
    kind = (identifier_type or "").strip().upper()
    if kind not in _IDENTIFIER_TYPES:
        raise ToolError("identifier_type must be EMAIL, PHONE or EXTERNAL_ID.")
    cleaned = list(dict.fromkeys(str(i).strip() for i in identifiers if str(i).strip()))
    if not 1 <= len(cleaned) <= _MAX_PARTICIPANTS:
        raise ToolError(f"Pass between 1 and {_MAX_PARTICIPANTS} identifiers.")

    def apply() -> dict[str, Any]:
        answer = api.request(
            account,
            "POST",
            f"/moments/1/flows/{int(flow_id)}/participants",
            json={
                "participants": [
                    {"identifyBy": {"identifier": value, "type": kind}}
                    for value in cleaned
                ]
            },
        )
        return {"operation_id": (answer or {}).get("operationId")}

    return propose_or_apply(
        account=account,
        action="add_flow_participants",
        request={"flow_id": int(flow_id), "type": kind, "identifiers": cleaned},
        current=None,
        summary={
            "flow_id": int(flow_id),
            "adds_people": len(cleaned),
            "identified_by": kind,
            "first_identifiers": cleaned[:5],
            "warning": "The flow starts for these people and may send messages.",
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def flows_get_add_report(
    account: str, flow_id: int, operation_id: str
) -> dict[str, Any]:
    """Shows whether the people from flows_add_participants were accepted.

    Args:
        account: Account alias from accounts_list.
        flow_id: The flow's ID.
        operation_id: The operation ID returned when the people were added.
    """
    answer = api.request(
        account,
        "GET",
        f"/moments/1/flows/{int(flow_id)}/participants/report",
        params={"operationId": operation_id},
    )
    return {"report": answer}
