"""Tankerkönig Mobile: fuel prices at home and at a moving location."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
    CONF_TRACKER,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_MOBILE,
    STORAGE_VERSION,
)
from .coordinator import TankerkoenigLocationCoordinator

PLATFORMS = [Platform.SENSOR]

TankerkoenigConfigEntry = ConfigEntry[dict[str, TankerkoenigLocationCoordinator]]


async def async_setup_entry(hass: HomeAssistant, entry: TankerkoenigConfigEntry) -> bool:
    """Set up a coordinator per enabled location."""
    enabled = {
        LOCATION_HOME: entry.options.get(CONF_ENABLE_HOME, True),
        LOCATION_MOBILE: entry.options.get(CONF_ENABLE_MOBILE, True)
        and bool(entry.options.get(CONF_TRACKER)),
    }
    coordinators: dict[str, TankerkoenigLocationCoordinator] = {}
    for location, is_enabled in enabled.items():
        if not is_enabled:
            continue
        coordinator = TankerkoenigLocationCoordinator(hass, entry, location)
        await coordinator.async_load()
        # Never raises on network errors: falls back to the restored data.
        await coordinator.async_config_entry_first_refresh()
        entry.async_on_unload(coordinator.async_shutdown)
        if location == LOCATION_MOBILE:
            entry.async_on_unload(coordinator.async_track_tracker())
        coordinators[location] = coordinator

    entry.runtime_data = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TankerkoenigConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: TankerkoenigConfigEntry) -> None:
    """Delete the persisted responses."""
    for location in (LOCATION_HOME, LOCATION_MOBILE):
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{location}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: TankerkoenigConfigEntry) -> None:
    """Reload after the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
