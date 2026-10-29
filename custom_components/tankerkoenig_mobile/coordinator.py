"""Coordinator fetching Tankerkönig data for one location (home or mobile)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_API_KEY
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .api import TankerkoenigError, async_get_stations
from .const import (
    CONF_DWELL_MINUTES,
    CONF_FIXED_TIMES,
    CONF_FUEL_TYPE,
    CONF_PAUSE_ZONES,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_STATION_FILTER,
    CONF_STATIONARY_TOLERANCE,
    CONF_TRACKER,
    DEFAULT_DWELL_MINUTES,
    DEFAULT_FUEL_TYPE,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATIONARY_TOLERANCE,
    DOMAIN,
    FUEL_TYPES,
    LOCATION_HOME,
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


def _split_list(raw: str | None) -> list[str]:
    """Split a comma or newline separated text field."""
    return [item.strip() for item in re.split(r"[,\n]", raw or "") if item.strip()]


def parse_station_filter(raw: str | None) -> set[str]:
    """Split the allow list into lowercase entries."""
    return {item.casefold() for item in _split_list(raw)}


def parse_fixed_times(raw: str | None) -> list[time]:
    """Parse "HH:MM" entries; raise ValueError on an invalid entry."""
    times = []
    for item in _split_list(raw):
        if (parsed := dt_util.parse_time(item)) is None:
            raise ValueError(item)
        times.append(parsed)
    return times


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
        if self._fuel_type not in FUEL_TYPES:
            # e.g. "all" from versions before 0.4.0
            self._fuel_type = DEFAULT_FUEL_TYPE
        self._tracker: str | None = entry.options.get(CONF_TRACKER)
        self._dwell = timedelta(
            minutes=float(entry.options.get(CONF_DWELL_MINUTES, DEFAULT_DWELL_MINUTES))
        )
        self._tolerance_m = float(
            entry.options.get(CONF_STATIONARY_TOLERANCE, DEFAULT_STATIONARY_TOLERANCE)
        )
        # The allow list only applies to home; on the road all stations are kept.
        self._allowed = (
            parse_station_filter(entry.options.get(CONF_STATION_FILTER))
            if location == LOCATION_HOME
            else set()
        )
        self._pause_zones: list[str] = entry.options.get(CONF_PAUSE_ZONES) or []
        try:
            self._fixed_times = parse_fixed_times(entry.options.get(CONF_FIXED_TIMES))
        except ValueError:
            self._fixed_times = []
        self.paused = False
        # On the road: no requests while driving. Assume standing still at start.
        self.moving = False
        self._anchor: tuple[float, float] | None = None
        self._unsub_dwell: Callable[[], None] | None = None
        # Called when the vehicle enters a pause zone (set up in __init__.py).
        self.on_enter_pause_zone: Callable[[], None] | None = None
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

    def _in_pause_zone(self, coords: tuple[float, float]) -> bool:
        """Whether the coordinates lie within one of the pause zones."""
        for zone_id in self._pause_zones:
            zone = self.hass.states.get(zone_id)
            if (center := coordinates_from_state(zone)) is None:
                continue
            dist = distance(*center, *coords)
            if dist is not None and dist <= float(zone.attributes.get("radius", 0)):
                return True
        return False

    async def _async_update_data(self) -> LocationData | None:
        if (coords := self._coordinates()) is None:
            self._handle_failure(f"No coordinates available from {self._tracker}")
            return self.data

        if self.location == LOCATION_MOBILE:
            if self._anchor is None:
                self._anchor = coords
            self.paused = self._in_pause_zone(coords)
            if self.paused or self.moving:
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
        if not self.moving:
            retry = MIN_FETCH_GAP * 2 ** min(self._failures - 1, 6)
            self.update_interval = min(retry, self._regular_interval)

    @callback
    def async_track_tracker(self) -> Callable[[], None]:
        """Follow the tracker to detect driving, parking and the pause zones."""
        assert self._tracker is not None
        return async_track_state_change_event(
            self.hass, [self._tracker], self._handle_tracker_change
        )

    @callback
    def _handle_tracker_change(self, event: Event[EventStateChangedData]) -> None:
        if (coords := coordinates_from_state(event.data["new_state"])) is None:
            return
        if self._in_pause_zone(coords):
            self._cancel_dwell()
            self._anchor = coords
            if not self.paused or self.moving:
                entered = not self.paused
                self.paused = True
                self.moving = False
                self.update_interval = self._regular_interval
                self.async_update_listeners()
                if entered and self.on_enter_pause_zone is not None:
                    self.on_enter_pause_zone()
            return
        if self.paused:
            # Leaving the pause zone means the trip starts.
            self.paused = False
            self._start_moving(coords)
            return
        if self._anchor is None:
            # First position (tracker was unavailable so far): treat as parked.
            self._anchor = coords
            self.request_refresh_rate_limited()
            return
        moved_m = distance(*self._anchor, *coords)
        if moved_m is not None and moved_m > self._tolerance_m:
            self._start_moving(coords)

    @callback
    def _start_moving(self, coords: tuple[float, float]) -> None:
        """Pause requests while driving and (re)start the parking timer."""
        self._anchor = coords
        if not self.moving:
            self.moving = True
            self.update_interval = None
            self.async_update_listeners()
        self._cancel_dwell()
        self._unsub_dwell = async_call_later(
            self.hass, self._dwell.total_seconds(), self._handle_parked
        )

    @callback
    def _handle_parked(self, _now: datetime) -> None:
        """Stood still long enough: fetch now, then poll regularly."""
        self._unsub_dwell = None
        self.moving = False
        self.update_interval = self._regular_interval
        self.async_update_listeners()
        self.request_refresh_rate_limited()

    @callback
    def _cancel_dwell(self) -> None:
        if self._unsub_dwell is not None:
            self._unsub_dwell()
            self._unsub_dwell = None

    @callback
    def async_track_fixed_times(self) -> Callable[[], None]:
        """Refresh at the configured local times."""

        @callback
        def _fire(_now: datetime) -> None:
            self.request_refresh_rate_limited()

        unsubs = [
            async_track_time_change(
                self.hass, _fire, hour=at.hour, minute=at.minute, second=at.second
            )
            for at in self._fixed_times
        ]

        @callback
        def _unsub_all() -> None:
            for unsub in unsubs:
                unsub()

        return _unsub_all

    @callback
    def request_refresh_rate_limited(self) -> None:
        """Refresh now, or as soon as the 5 minute gap to the last request allows."""
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
        """Cancel pending timers."""
        self._cancel_dwell()
        if self._unsub_delayed_refresh is not None:
            self._unsub_delayed_refresh()
            self._unsub_delayed_refresh = None
        await super().async_shutdown()
