"""Sign-in with Google and the list of people allowed to use the server.

FastMCP's Google provider handles the sign-in. On top of that, only the
addresses in MESSAGING_MCP_ALLOWED_EMAILS may call a tool, because the tools
can send messages from every configured account.
"""

import os
from typing import Any

from fastmcp.exceptions import ToolError

CLIENT_ID_ENV_VAR = "MESSAGING_MCP_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "MESSAGING_MCP_OAUTH_CLIENT_SECRET"
BASE_URL_ENV_VAR = "MESSAGING_MCP_BASE_URL"
SIGNING_KEY_ENV_VAR = "MESSAGING_MCP_JWT_SIGNING_KEY"
ALLOWED_EMAILS_ENV_VAR = "MESSAGING_MCP_ALLOWED_EMAILS"
FIRESTORE_PROJECT_ENV_VAR = "MESSAGING_MCP_FIRESTORE_PROJECT"
FIRESTORE_DATABASE_ENV_VAR = "MESSAGING_MCP_FIRESTORE_DATABASE"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def sign_in_configured() -> bool:
    return bool(
        os.environ.get(CLIENT_ID_ENV_VAR)
        and os.environ.get(CLIENT_SECRET_ENV_VAR)
    )


def allowed_emails() -> set[str]:
    raw = os.environ.get(ALLOWED_EMAILS_ENV_VAR, "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _signed_in_claims() -> dict[str, Any] | None:
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        return None
    return dict(token.claims or {})


def require_allowed_user() -> str | None:
    """Returns the caller's email, or raises when the caller is not allowed.

    Without sign-in configured (local use over stdio) there is no caller to
    check and None is returned. With sign-in configured the check fails
    closed: an empty allow list lets nobody in.
    """
    if not sign_in_configured():
        return None
    claims = _signed_in_claims()
    if claims is None:
        raise ToolError("Not signed in.")
    email = str(claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified"):
        raise ToolError("The sign-in did not provide a verified email address.")
    if email not in allowed_emails():
        raise ToolError(
            f"{email} is not allowed to use this server. Allowed addresses "
            f"are set in {ALLOWED_EMAILS_ENV_VAR}."
        )
    return email


def _session_storage() -> Any:
    """Firestore storage for sign-ins, so they survive a restart."""
    project = os.environ.get(FIRESTORE_PROJECT_ENV_VAR)
    database = os.environ.get(FIRESTORE_DATABASE_ENV_VAR)
    if not (project or database):
        return None
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.oauth_proxy.proxy import derive_jwt_key
    from key_value.aio.stores.firestore import (
        FirestoreStore,
        FirestoreV1CollectionSanitizationStrategy,
        FirestoreV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption.fernet import (
        FernetEncryptionWrapper,
    )

    # Client ids can be URLs, and Firestore rejects '/' in a document id.
    store = FirestoreStore(
        project=project,
        database=database,
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
        collection_sanitization_strategy=(
            FirestoreV1CollectionSanitizationStrategy()
        ),
    )
    secret = os.environ.get(SIGNING_KEY_ENV_VAR) or os.environ.get(
        CLIENT_SECRET_ENV_VAR
    )
    key = derive_jwt_key(
        high_entropy_material=secret, salt="messaging-mcp-session-storage"
    )
    return FernetEncryptionWrapper(
        key_value=store, fernet=Fernet(key), raise_on_decryption_error=False
    )


def build_auth() -> Any:
    """Returns the Google sign-in provider, or None when none is configured."""
    if not sign_in_configured():
        return None
    from fastmcp.server.auth.providers.google import GoogleProvider

    kwargs: dict[str, Any] = {
        "client_id": os.environ[CLIENT_ID_ENV_VAR],
        "client_secret": os.environ[CLIENT_SECRET_ENV_VAR],
        "base_url": os.environ.get(BASE_URL_ENV_VAR, "http://localhost:8080"),
        "required_scopes": SCOPES,
    }
    signing_key = os.environ.get(SIGNING_KEY_ENV_VAR)
    if signing_key:
        kwargs["jwt_signing_key"] = signing_key
    storage = _session_storage()
    if storage is not None:
        kwargs["client_storage"] = storage
    return GoogleProvider(**kwargs)
