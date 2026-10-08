from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gsoi_assistant.api.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


# Declares the "Bearer" scheme in OpenAPI so /docs shows the Authorize button.
_bearer = HTTPBearer(auto_error=False, description="The value of GSOI_API_TOKEN")


def require_token(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Single-user bearer auth. Returns the owner's user_id."""
    expected = container.settings.api_token.get_secret_value()
    supplied = credentials.credentials.strip() if credentials else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return container.settings.owner_id


UserDep = Annotated[str, Depends(require_token)]
