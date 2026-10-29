"""Tests for setup, robustness, persistence, filtering and movement refresh."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankerkoenig_mobile.const import API_URL, DOMAIN

from .conftest import API_KEY, STATIONS_RESPONSE, TRACKER, make_options

# Fixed coordinates of the hass test fixture's home location are used for home.
WOMO = {"latitude": 48.0, "longitude": 9.0}
WOMO_NEARBY = {"latitude": 48.004, "longitude": 9.0}  # ~450 m
WOMO_FAR = {"latitude": 48.1, "longitude": 9.0}  # ~11 km


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


async def test_mobile_refreshes_on_movement(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Small moves are ignored, large moves refresh, respecting the 5 min gap."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    hass.states.async_set(TRACKER, "not_home", WOMO)
    entry = await _setup(hass, enable_home=False, enable_mobile=True)
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][1].query["lat"] == "48.000000"

    hass.states.async_set(TRACKER, "not_home", WOMO_NEARBY)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1

    # Far move right after the last request: delayed until 5 min have passed.
    hass.states.async_set(TRACKER, "not_home", WOMO_FAR)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1

    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2
    assert aioclient_mock.mock_calls[1][1].query["lat"] == "48.100000"

    state = hass.states.get(_entity_id(hass, entry, "mobile", "stations"))
    assert state.attributes["query_latitude"] == WOMO_FAR["latitude"]


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
