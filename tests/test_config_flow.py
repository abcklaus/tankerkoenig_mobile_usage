"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankerkoenig_mobile.const import (
    API_URL,
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
    CONF_FUEL_TYPE,
    CONF_STATION_FILTER,
    CONF_TRACKER,
    DOMAIN,
    SECTION_MOBILE,
)

from .conftest import API_KEY, STATIONS_RESPONSE, TRACKER, make_options, nested_input


def _user_input(**overrides):
    return {CONF_API_KEY: API_KEY, **nested_input(**overrides)}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid key creates the entry; settings end up in the options."""
    aioclient_mock.get(API_URL, json=STATIONS_RESPONSE)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    with patch(
        "custom_components.tankerkoenig_mobile.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _user_input(enable_mobile=True)
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: API_KEY}
    # Options are stored flat, without the form sections.
    assert result["options"] == make_options(enable_mobile=True, fixed_times="")
    assert result["options"][CONF_TRACKER] == TRACKER


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ok=false with an apikey message is reported as invalid_auth."""
    aioclient_mock.get(
        API_URL,
        json={"ok": False, "status": "error", "message": "apikey nicht angegeben, falsch, oder im falschen Format"},
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _user_input())
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network errors are reported as cannot_connect."""
    aioclient_mock.get(API_URL, exc=aiohttp.ClientConnectionError())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _user_input())
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_requires_tracker(hass: HomeAssistant) -> None:
    """Mobile without a tracker is rejected before any request."""
    result = await _start(hass)
    user_input = _user_input(enable_mobile=True)
    del user_input[SECTION_MOBILE][CONF_TRACKER]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"base": "tracker_required"}


async def test_user_flow_requires_a_location(hass: HomeAssistant) -> None:
    """At least one location must be enabled."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input(enable_home=False, enable_mobile=False)
    )
    assert result["errors"] == {"base": "no_location"}


async def test_user_flow_invalid_time(hass: HomeAssistant) -> None:
    """Fixed times must be HH:MM."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input(fixed_times="12:00, 25:00")
    )
    assert result["errors"] == {"base": "invalid_time"}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Options can be changed without touching the API key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY},
        options=make_options(station_filter="ARAL", move_threshold_km=2),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    # The form shows the stored flat options inside their sections.
    schema = result["data_schema"].schema
    home_section = next(v for k, v in schema.items() if str(k) == "home")
    filter_key = next(k for k in home_section.schema.schema if str(k) == CONF_STATION_FILTER)
    assert filter_key.description == {"suggested_value": "ARAL"}

    new_input = nested_input(enable_home=False, enable_mobile=True, station_filter="JET")
    with patch(
        "custom_components.tankerkoenig_mobile.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], new_input
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENABLE_HOME] is False
    assert entry.options[CONF_ENABLE_MOBILE] is True
    assert entry.options[CONF_STATION_FILTER] == "JET"
    assert "move_threshold_km" not in entry.options
    assert entry.data == {CONF_API_KEY: API_KEY}


async def test_fuel_type_all_is_rejected(hass: HomeAssistant) -> None:
    """Only a single fuel type can be selected."""
    result = await _start(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], _user_input(fuel_type="all")
        )


async def test_options_flow_drops_legacy_fuel_type_all(hass: HomeAssistant) -> None:
    """A stored "all" is not suggested; the form falls back to the default."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options=make_options(fuel_type="all")
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    fuel_key = next(k for k in result["data_schema"].schema if str(k) == CONF_FUEL_TYPE)
    assert (fuel_key.description or {}).get("suggested_value") != "all"
    assert fuel_key.default() == "diesel"
