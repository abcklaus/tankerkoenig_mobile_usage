"""Sensors exposing the raw Tankerkönig response."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TankerkoenigConfigEntry
from .const import DOMAIN, LOCATION_NAMES
from .coordinator import TankerkoenigLocationCoordinator

# Keys the API returns; kept out of the recorder because "stations" easily
# exceeds its 16 KB attribute limit and would bloat the database.
API_ATTRIBUTES = frozenset({"ok", "license", "data", "status", "stations"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TankerkoenigConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors for each enabled location."""
    entities: list[SensorEntity] = []
    for coordinator in entry.runtime_data.values():
        entities.append(TankerkoenigStationsSensor(coordinator))
        entities.append(TankerkoenigLastSuccessSensor(coordinator))
    async_add_entities(entities)


class TankerkoenigEntity(CoordinatorEntity[TankerkoenigLocationCoordinator]):
    """Common device info for one location."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TankerkoenigLocationCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        device_id = f"{coordinator.config_entry.entry_id}_{coordinator.location}"
        self._attr_translation_key = key
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=LOCATION_NAMES[coordinator.location],
            manufacturer="Tankerkönig",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://creativecommons.tankerkoenig.de",
        )


class TankerkoenigStationsSensor(TankerkoenigEntity, SensorEntity):
    """State "ok" once data exists; attributes are the API response 1:1."""

    _unrecorded_attributes = API_ATTRIBUTES

    def __init__(self, coordinator: TankerkoenigLocationCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "stations")

    @property
    def native_value(self) -> str | None:
        """Return ok as soon as a response is available."""
        return "ok" if self.coordinator.data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the API response plus fetch metadata."""
        data = self.coordinator.data
        attrs: dict[str, Any] = dict(self.coordinator.filtered_response or {})
        attrs["last_success"] = data.last_success.isoformat() if data else None
        attrs["last_error"] = self.coordinator.last_error
        attrs["query_latitude"] = data.latitude if data else None
        attrs["query_longitude"] = data.longitude if data else None
        return attrs


class TankerkoenigLastSuccessSensor(TankerkoenigEntity, SensorEntity):
    """Timestamp of the last successful API call."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TankerkoenigLocationCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "last_success")

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last successful call."""
        data = self.coordinator.data
        return data.last_success if data else None
