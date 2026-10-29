"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tankerkoenig_mobile.const import (
    API_URL,
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
    CONF_TRACKER,
    DOMAIN,
)

from .conftest import API_KEY, STATIONS_RESPONSE, TRACKER, make_options


def _user_input(**overrides):
    return {CONF_API_KEY: API_KEY, **make_options(**overrides)}


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
    assert result["options"][CONF_TRACKER] == TRACKER
    assert CONF_API_KEY not in result["options"]


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
    del user_input[CONF_TRACKER]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {CONF_TRACKER: "tracker_required"}


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
    assert result["errors"] == {"fixed_times": "invalid_time"}


async def test_options_flow(hass: HomeAssistant) -> None:
    """Options can be changed without touching the API key."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: API_KEY}, options=make_options())
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    new_options = make_options(enable_home=False, enable_mobile=True, station_filter="JET")
    with patch(
        "custom_components.tankerkoenig_mobile.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], new_options
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ENABLE_HOME] is False
    assert entry.options[CONF_ENABLE_MOBILE] is True
    assert entry.data == {CONF_API_KEY: API_KEY}
