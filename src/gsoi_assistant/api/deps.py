from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from gsoi_assistant.api.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def require_token(
    container: ContainerDep, authorization: Annotated[str | None, Header()] = None
) -> str:
    """Single-user bearer auth. Returns the owner's user_id."""
    expected = container.settings.api_token.get_secret_value()
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return container.settings.owner_id


UserDep = Annotated[str, Depends(require_token)]
