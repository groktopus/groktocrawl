"""Credential-derived session ownership; never accept scope from a payload."""

import hashlib
from typing import Any

from fastapi import Request

from .exceptions import NotFoundError


def request_scope(request: Request) -> str:
    from .auth import request_credential

    credential = request_credential(request)
    return (
        "key:" + hashlib.sha256(credential.encode()).hexdigest()[:32]
        if credential
        else "anonymous"
    )


async def authorize_session(store: Any, session_id: str, request: Request) -> dict:
    session = await store.aget(session_id)
    if session is None or (
        session.get("owner_scope") is not None
        and session["owner_scope"] != request_scope(request)
    ):
        # Do not reveal existence to a foreign principal.
        raise NotFoundError(detail="Session not found or expired")
    return session
