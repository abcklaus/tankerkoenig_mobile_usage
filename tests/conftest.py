"""Fixtures for Tankerkönig Mobile tests."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.tankerkoenig_mobile.const import (
    CONF_DWELL_MINUTES,
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
    CONF_FUEL_TYPE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_STATION_FILTER,
    CONF_STATIONARY_TOLERANCE,
    CONF_TRACKER,
    SECTION_HOME,
    SECTION_MOBILE,
)

pytest_plugins = "pytest_homeassistant_custom_component"

API_KEY = "00000000-0000-0000-0000-000000000000"
TRACKER = "device_tracker.womo"

STATIONS_RESPONSE: dict[str, Any] = {
    "ok": True,
    "license": "CC BY 4.0 -  https://creativecommons.tankerkoenig.de",
    "data": "MTS-K",
    "status": "ok",
    "stations": [
        {
            "id": "station-aral",
            "name": "Aral Tankstelle",
            "brand": "ARAL",
            "street": "Hauptstr.",
            "place": "Musterstadt",
            "lat": 48.7,
            "lng": 8.9,
            "dist": 1.2,
            "price": 1.689,
            "isOpen": True,
            "houseNumber": "1",
            "postCode": 71000,
        },
        {
            "id": "station-jet",
            "name": "JET",
            "brand": "JET",
            "street": "Bahnhofstr.",
            "place": "Musterstadt",
            "lat": 48.71,
            "lng": 8.91,
            "dist": 2.5,
            "price": 1.649,
            "isOpen": True,
            "houseNumber": "5",
            "postCode": 71000,
        },
        {
            "id": "station-free",
            "name": "Freie Tankstelle",
            "brand": "",
            "street": "Feldweg",
            "place": "Musterstadt",
            "lat": 48.72,
            "lng": 8.92,
            "dist": 3.1,
            "price": 1.629,
            "isOpen": False,
            "houseNumber": "",
            "postCode": 71000,
        },
    ],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the custom integration in every test."""
    return


def make_options(**overrides: Any) -> dict[str, Any]:
    """Return a full options dict with sensible test defaults."""
    options: dict[str, Any] = {
        CONF_ENABLE_HOME: True,
        CONF_ENABLE_MOBILE: False,
        CONF_RADIUS: 5,
        CONF_FUEL_TYPE: "diesel",
        CONF_STATION_FILTER: "",
        CONF_SCAN_INTERVAL: 167,
        CONF_DWELL_MINUTES: 10,
        CONF_STATIONARY_TOLERANCE: 200,
    }
    options.update(overrides)
    if options[CONF_ENABLE_MOBILE]:
        options.setdefault(CONF_TRACKER, TRACKER)
    return options


def nested_input(**overrides: Any) -> dict[str, Any]:
    """Return form input with the home/mobile sections, as the frontend sends it."""
    flat = make_options(**overrides)
    home_keys = {CONF_ENABLE_HOME, CONF_STATION_FILTER}
    mobile_keys = {
        CONF_ENABLE_MOBILE,
        CONF_TRACKER,
        "pause_zones",
        CONF_DWELL_MINUTES,
        CONF_STATIONARY_TOLERANCE,
    }
    nested: dict[str, Any] = {SECTION_HOME: {}, SECTION_MOBILE: {}}
    for key, value in flat.items():
        if key in home_keys:
            nested[SECTION_HOME][key] = value
        elif key in mobile_keys:
            nested[SECTION_MOBILE][key] = value
        else:
            nested[key] = value
    return nested
