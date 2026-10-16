import json

import httpx

from ..schemas import SafeError


async def request_json(client, method, url, *, limit=4 * 1024 * 1024, **kwargs):
    try:
        async with client.stream(method, url, **kwargs) as r:
            if r.status_code >= 400:
                raise SafeError(
                    f"backend_http_{r.status_code}"
                    if r.status_code in (429, 502, 503, 401, 403)
                    else "backend_http_error",
                    r.status_code in (429, 502, 503),
                )
            data = bytearray()
            async for part in r.aiter_bytes():
                data.extend(part)
                if len(data) > limit:
                    raise SafeError("backend_response_limit")
            try:
                return json.loads(data)
            except (ValueError, TypeError):
                raise SafeError("backend_invalid_json") from None
    except SafeError:
        raise
    except (httpx.TimeoutException, httpx.TransportError):
        raise SafeError("backend_unavailable", True) from None
