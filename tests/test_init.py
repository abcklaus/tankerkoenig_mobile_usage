"""Tests for setup, robustness, persistence, filtering, movement, zones and fixed times."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankerkoenig_mobile.const import API_URL, DOMAIN

from .conftest import API_KEY, STATIONS_RESPONSE, TRACKER, make_options

# Fixed coordinates of the hass test fixture's home location are used for home.
WOMO = {"latitude": 48.0, "longitude": 9.0}
WOMO_JITTER = {"latitude": 48.0009, "longitude": 9.0}  # ~100 m
WOMO_NEARBY = {"latitude": 48.004, "longitude": 9.0}  # ~450 m
WOMO_FAR = {"latitude": 48.1, "longitude": 9.0}  # ~11 km
HOME_ZONE = {"latitude": 48.0, "longitude": 9.0, "radius": 1000}


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options=make_options(**options)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, location: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{location}_{key}"
    )
    assert entity_id is not None
    return entity_id


async def test_home_sensor_exposes_raw_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """State is ok and attributes match the API response 1:1."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    entry = await _setup(hass)

    state = hass.states.get(_entity_id(hass, entry, "home", "stations"))
    assert state.state == "ok"
    for key, value in STATIONS_RESPONSE.items():
        assert state.attributes[key] == value
    assert state.attributes["last_success"] is not None
    assert state.attributes["last_error"] is None

    url = aioclient_mock.mock_calls[0][1]
    assert url.query["type"] == "diesel"
    assert url.query["sort"] == "dist"
    assert url.query["rad"] == "5"

    last_success = hass.states.get(_entity_id(hass, entry, "home", "last_success"))
    assert last_success.state not in ("unknown", "unavailable")

    # Mobile disabled: no entities for it.
    assert (
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_mobile_stations")
        is None
    )


async def test_failure_keeps_last_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network errors and ok=false keep the previous response and timestamp."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    entry = await _setup(hass)
    entity_id = _entity_id(hass, entry, "home", "stations")
    before = hass.states.get(entity_id)
    coordinator = entry.runtime_data["home"]

    for failure in (
        {"exc": aiohttp.ClientConnectionError()},
        {"exc": TimeoutError()},
        {"json": {"ok": False, "message": "rate limit"}},
        {"status": 500},
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(API_URL, **failure)
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state.state == "ok"
        assert state.attributes["stations"] == STATIONS_RESPONSE["stations"]
        assert state.attributes["last_success"] == before.attributes["last_success"]
        assert state.attributes["last_error"]
        assert API_KEY not in state.attributes["last_error"]


async def test_restores_data_without_network(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict[str, Any]
) -> None:
    """After a restart without network the stored response is shown."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    entry = await _setup(hass)
    store_key = f"{DOMAIN}.{entry.entry_id}.home"
    assert hass_storage[store_key]["data"]["response"] == STATIONS_RESPONSE
    stored_success = hass_storage[store_key]["data"]["last_success"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_URL, exc=aiohttp.ClientConnectionError())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, entry, "home", "stations"))
    assert state.state == "ok"
    assert state.attributes["stations"] == STATIONS_RESPONSE["stations"]
    assert state.attributes["last_success"] == stored_success
    assert state.attributes["last_error"]


async def test_station_filter_by_id_and_brand(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The allow list matches station ids and brands (case-insensitive)."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    entry = await _setup(hass, station_filter="station-free,\n jet ")

    state = hass.states.get(_entity_id(hass, entry, "home", "stations"))
    assert [s["id"] for s in state.attributes["stations"]] == ["station-jet", "station-free"]
    assert state.attributes["license"] == STATIONS_RESPONSE["license"]


async def test_station_filter_not_applied_on_the_road(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The allow list only applies to home."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set(TRACKER, "not_home", WOMO)
    entry = await _setup(hass, enable_mobile=True, station_filter="JET")

    home = hass.states.get(_entity_id(hass, entry, "home", "stations"))
    mobile = hass.states.get(_entity_id(hass, entry, "mobile", "stations"))
    assert [s["id"] for s in home.attributes["stations"]] == ["station-jet"]
    assert mobile.attributes["stations"] == STATIONS_RESPONSE["stations"]
    assert "vehicle_moving" not in home.attributes


async def test_mobile_fetches_after_standstill(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No requests while driving; fetch once parked for the dwell time, then poll."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set(TRACKER, "not_home", WOMO)
    entry = await _setup(hass, enable_home=False, enable_mobile=True)
    entity_id = _entity_id(hass, entry, "mobile", "stations")
    assert aioclient_mock.call_count == 1
    assert hass.states.get(entity_id).attributes["vehicle_moving"] is False

    # GPS jitter within the tolerance does not count as driving.
    hass.states.async_set(TRACKER, "not_home", WOMO_JITTER)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["vehicle_moving"] is False

    # Start driving.
    hass.states.async_set(TRACKER, "not_home", WOMO_NEARBY)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["vehicle_moving"] is True

    # Still driving 8 minutes later: the dwell timer restarts.
    freezer.tick(timedelta(minutes=8))
    hass.states.async_set(TRACKER, "not_home", WOMO_FAR)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=8))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1

    # Interval ticks while driving do not query either.
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1

    # Parked for 10 minutes at the far location: fetch.
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2
    assert aioclient_mock.mock_calls[1][1].query["lat"] == "48.100000"
    state = hass.states.get(entity_id)
    assert state.attributes["vehicle_moving"] is False
    assert state.attributes["query_latitude"] == WOMO_FAR["latitude"]

    # Parked: the regular interval applies again.
    freezer.tick(timedelta(minutes=168))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 3


async def test_fixed_times_skipped_while_driving(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Fixed times do not query on the road while the vehicle is moving."""
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 29, 11, 50, tzinfo=tz))
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set(TRACKER, "not_home", WOMO)
    await _setup(hass, enable_home=False, enable_mobile=True, fixed_times="12:00")
    assert aioclient_mock.call_count == 1

    freezer.move_to(datetime(2026, 10, 29, 11, 55, tzinfo=tz))
    hass.states.async_set(TRACKER, "not_home", WOMO_FAR)
    await hass.async_block_till_done()

    target = datetime(2026, 10, 29, 12, 0, tzinfo=tz)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1


async def test_mobile_without_coordinates_keeps_waiting(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unavailable tracker does not cause a request and keeps the sensor unknown."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set(TRACKER, "unavailable")
    entry = await _setup(hass, enable_home=False, enable_mobile=True)

    assert aioclient_mock.call_count == 0
    state = hass.states.get(_entity_id(hass, entry, "mobile", "stations"))
    assert state.state == "unknown"
    assert state.attributes["last_error"]

    hass.states.async_set(TRACKER, "not_home", WOMO)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1
    assert hass.states.get(_entity_id(hass, entry, "mobile", "stations")).state == "ok"


async def test_mobile_paused_in_zone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No requests inside the home zone; leaving it starts a trip."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set("zone.home", "0", HOME_ZONE)
    hass.states.async_set(TRACKER, "home", WOMO)
    entry = await _setup(
        hass, enable_home=False, enable_mobile=True, pause_zones=["zone.home"]
    )
    entity_id = _entity_id(hass, entry, "mobile", "stations")

    assert aioclient_mock.call_count == 0
    state = hass.states.get(entity_id)
    assert state.attributes["paused_in_zone"] is True
    assert state.attributes["last_error"] is None

    # Moving inside the zone and regular interval ticks do not query.
    hass.states.async_set(TRACKER, "home", WOMO_NEARBY)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=170))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0
    assert hass.states.get(entity_id).attributes["last_error"] is None

    hass.states.async_set(TRACKER, "not_home", WOMO_FAR)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0
    state = hass.states.get(entity_id)
    assert state.attributes["paused_in_zone"] is False
    assert state.attributes["vehicle_moving"] is True

    freezer.tick(timedelta(minutes=10, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1
    state = hass.states.get(entity_id)
    assert state.state == "ok"
    assert state.attributes["vehicle_moving"] is False


async def test_entering_zone_refreshes_home(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Arriving in the pause zone fetches the home prices right away."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set("zone.home", "0", HOME_ZONE)
    hass.states.async_set(TRACKER, "not_home", WOMO_FAR)
    entry = await _setup(
        hass, enable_home=True, enable_mobile=True, pause_zones=["zone.home"]
    )
    assert aioclient_mock.call_count == 2

    freezer.tick(timedelta(minutes=6))
    hass.states.async_set(TRACKER, "home", WOMO)
    await hass.async_block_till_done()

    assert aioclient_mock.call_count == 3
    assert aioclient_mock.mock_calls[-1][1].query["lat"] == f"{hass.config.latitude:.6f}"
    mobile = hass.states.get(_entity_id(hass, entry, "mobile", "stations"))
    assert mobile.attributes["paused_in_zone"] is True


async def test_fixed_times_trigger_refresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A configured local time always triggers a request."""
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 29, 11, 50, tzinfo=tz))
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    await _setup(hass, fixed_times="12:00")
    assert aioclient_mock.call_count == 1

    target = datetime(2026, 10, 29, 12, 0, tzinfo=tz)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2


async def test_fixed_times_skipped_in_zone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The pause zone takes precedence over fixed times on the road."""
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 10, 29, 11, 50, tzinfo=tz))
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set("zone.home", "0", HOME_ZONE)
    hass.states.async_set(TRACKER, "home", WOMO)
    await _setup(
        hass,
        enable_home=False,
        enable_mobile=True,
        pause_zones=["zone.home"],
        fixed_times="12:00",
    )

    target = datetime(2026, 10, 29, 12, 0, tzinfo=tz)
    freezer.move_to(target)
    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0


async def test_legacy_fuel_type_all_falls_back_to_diesel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entries created with fuel_type "all" (before 0.4.0) query diesel."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    await _setup(hass, fuel_type="all")
    assert aioclient_mock.mock_calls[0][1].query["type"] == "diesel"
