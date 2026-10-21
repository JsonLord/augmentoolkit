"""Fail-closed operator authentication for privileged parent routes."""

import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    expected = os.environ.get("WORKER_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Parent API authentication is not configured")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
