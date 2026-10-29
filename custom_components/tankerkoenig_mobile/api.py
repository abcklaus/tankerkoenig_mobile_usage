"""Minimal client for the Tankerkönig list.php endpoint."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_URL, REQUEST_TIMEOUT


class TankerkoenigError(Exception):
    """Request failed (network, HTTP or API error)."""


class TankerkoenigAuthError(TankerkoenigError):
    """API key was rejected."""


async def async_get_stations(
    session: aiohttp.ClientSession,
    api_key: str,
    latitude: float,
    longitude: float,
    radius: float,
    fuel_type: str,
) -> dict[str, Any]:
    """Query list.php and return the raw JSON response.

    Error messages never contain the request URL, so the API key does not end
    up in the log.
    """
    params = {
        "lat": f"{latitude:.6f}",
        "lng": f"{longitude:.6f}",
        "rad": f"{radius:g}",
        "sort": "dist",
        "type": fuel_type,
        "apikey": api_key,
    }
    try:
        async with session.get(
            API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            if resp.status in (401, 403):
                raise TankerkoenigAuthError(f"HTTP {resp.status}")
            if resp.status != 200:
                raise TankerkoenigError(f"HTTP {resp.status}")
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TankerkoenigError(f"Connection failed ({type(err).__name__})") from None
    except ValueError:
        raise TankerkoenigError("Invalid JSON response") from None

    if not isinstance(payload, dict):
        raise TankerkoenigError("Unexpected response format")
    if payload.get("ok") is not True:
        message = str(payload.get("message") or "API returned ok=false")
        if "apikey" in message.lower():
            raise TankerkoenigAuthError(message)
        raise TankerkoenigError(message)
    return payload
