"""Coordinator fetching Tankerkönig data for one location (home or mobile)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_API_KEY
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .api import TankerkoenigError, async_get_stations
from .const import (
    CONF_FUEL_TYPE,
    CONF_MOVE_THRESHOLD,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_STATION_FILTER,
    CONF_TRACKER,
    DEFAULT_FUEL_TYPE,
    DEFAULT_MOVE_THRESHOLD,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOCATION_MOBILE,
    MIN_FETCH_GAP,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class LocationData:
    """Last successful API response for a location."""

    response: dict[str, Any]
    last_success: datetime
    latitude: float
    longitude: float

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the store."""
        return {
            "response": self.response,
            "last_success": self.last_success.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LocationData | None:
        """Deserialize from the store; None if the stored data is unusable."""
        try:
            last_success = dt_util.parse_datetime(raw["last_success"])
            if last_success is None:
                return None
            return cls(
                response=raw["response"],
                last_success=last_success,
                latitude=float(raw["latitude"]),
                longitude=float(raw["longitude"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def parse_station_filter(raw: str | None) -> set[str]:
    """Split the allow list (comma or newline separated) into lowercase entries."""
    return {item.strip().casefold() for item in re.split(r"[,\n]", raw or "") if item.strip()}


def filter_stations(response: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Return the response with only allowed stations (by id or brand)."""
    if not allowed or not isinstance(response.get("stations"), list):
        return response
    stations = [
        station
        for station in response["stations"]
        if str(station.get("id") or "").casefold() in allowed
        or str(station.get("brand") or "").strip().casefold() in allowed
    ]
    return {**response, "stations": stations}


def coordinates_from_state(state: State | None) -> tuple[float, float] | None:
    """Extract latitude/longitude from a tracker, person or zone state."""
    if state is None:
        return None
    try:
        return float(state.attributes[ATTR_LATITUDE]), float(state.attributes[ATTR_LONGITUDE])
    except (KeyError, TypeError, ValueError):
        return None


class TankerkoenigLocationCoordinator(DataUpdateCoordinator[LocationData | None]):
    """Fetch stations for one location.

    A failed request never marks the entities unavailable: the last successful
    response is kept (also across restarts via the store) and the error is
    exposed separately in ``last_error``.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, location: str) -> None:
        """Initialize the coordinator."""
        self._regular_interval = timedelta(
            minutes=max(int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)), 5)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {location}",
            update_interval=self._regular_interval,
        )
        self.location = location
        self.last_error: str | None = None
        self._api_key: str = entry.data[CONF_API_KEY]
        self._radius = float(entry.options.get(CONF_RADIUS, DEFAULT_RADIUS))
        self._fuel_type: str = entry.options.get(CONF_FUEL_TYPE, DEFAULT_FUEL_TYPE)
        self._tracker: str | None = entry.options.get(CONF_TRACKER)
        self._move_threshold_km = float(
            entry.options.get(CONF_MOVE_THRESHOLD, DEFAULT_MOVE_THRESHOLD)
        )
        self._allowed = parse_station_filter(entry.options.get(CONF_STATION_FILTER))
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{location}"
        )
        self._failures = 0
        self._last_attempt: datetime | None = None
        self._unsub_delayed_refresh: Callable[[], None] | None = None

    @property
    def filtered_response(self) -> dict[str, Any] | None:
        """API response with the station allow list applied."""
        if self.data is None:
            return None
        return filter_stations(self.data.response, self._allowed)

    async def async_load(self) -> None:
        """Restore the last successful response from disk."""
        if (raw := await self._store.async_load()) is not None:
            self.data = LocationData.from_dict(raw)

    async def async_remove_store(self) -> None:
        """Delete the persisted data."""
        await self._store.async_remove()

    def _coordinates(self) -> tuple[float, float] | None:
        if self.location != LOCATION_MOBILE:
            return self.hass.config.latitude, self.hass.config.longitude
        if self._tracker is None:
            return None
        return coordinates_from_state(self.hass.states.get(self._tracker))

    async def _async_update_data(self) -> LocationData | None:
        if (coords := self._coordinates()) is None:
            self._handle_failure(f"No coordinates available from {self._tracker}")
            return self.data

        self._last_attempt = dt_util.utcnow()
        try:
            response = await async_get_stations(
                async_get_clientsession(self.hass),
                self._api_key,
                coords[0],
                coords[1],
                self._radius,
                self._fuel_type,
            )
        except TankerkoenigError as err:
            self._handle_failure(str(err))
            return self.data

        if self._failures:
            _LOGGER.info("Tankerkönig (%s) reachable again", self.location)
        self._failures = 0
        self.last_error = None
        self.update_interval = self._regular_interval

        data = LocationData(response, dt_util.utcnow(), coords[0], coords[1])
        await self._store.async_save(data.as_dict())
        return data

    def _handle_failure(self, message: str) -> None:
        """Keep the old data, remember the error and retry sooner with backoff."""
        self._failures += 1
        self.last_error = message
        log = _LOGGER.warning if self._failures == 1 else _LOGGER.debug
        log("Tankerkönig (%s) update failed, keeping last data: %s", self.location, message)
        retry = MIN_FETCH_GAP * 2 ** min(self._failures - 1, 6)
        self.update_interval = min(retry, self._regular_interval)

    @callback
    def async_track_tracker(self) -> Callable[[], None]:
        """Refresh as soon as the tracker moved further than the threshold."""
        assert self._tracker is not None
        return async_track_state_change_event(
            self.hass, [self._tracker], self._handle_tracker_change
        )

    @callback
    def _handle_tracker_change(self, event: Event[EventStateChangedData]) -> None:
        if (coords := coordinates_from_state(event.data["new_state"])) is None:
            return
        if self.data is not None:
            moved_m = distance(self.data.latitude, self.data.longitude, *coords)
            if moved_m is not None and moved_m < self._move_threshold_km * 1000:
                return
        self._request_refresh_rate_limited()

    @callback
    def _request_refresh_rate_limited(self) -> None:
        if self._unsub_delayed_refresh is not None:
            return
        wait = 0.0
        if self._last_attempt is not None:
            wait = (self._last_attempt + MIN_FETCH_GAP - dt_util.utcnow()).total_seconds()
        if wait <= 0:
            self.config_entry.async_create_task(self.hass, self.async_request_refresh())
            return
        self._unsub_delayed_refresh = async_call_later(self.hass, wait, self._delayed_refresh)

    async def _delayed_refresh(self, _now: datetime) -> None:
        self._unsub_delayed_refresh = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel a pending movement refresh."""
        if self._unsub_delayed_refresh is not None:
            self._unsub_delayed_refresh()
            self._unsub_delayed_refresh = None
        await super().async_shutdown()
