"""Config and options flow for Tankerkönig Mobile."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TankerkoenigAuthError, TankerkoenigError, async_get_stations
from .const import (
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
    CONF_FIXED_TIMES,
    CONF_FUEL_TYPE,
    CONF_MOVE_THRESHOLD,
    CONF_PAUSE_ZONES,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_STATION_FILTER,
    CONF_TRACKER,
    DEFAULT_FUEL_TYPE,
    DEFAULT_MOVE_THRESHOLD,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FUEL_TYPES,
    MAX_RADIUS_KM,
)
from .coordinator import parse_fixed_times

SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENABLE_HOME, default=True): BooleanSelector(),
        vol.Required(CONF_ENABLE_MOBILE, default=True): BooleanSelector(),
        vol.Optional(CONF_TRACKER): EntitySelector(
            EntitySelectorConfig(domain=["device_tracker", "person", "zone"])
        ),
        # No default: the field must stay clearable in the options flow.
        vol.Optional(CONF_PAUSE_ZONES): EntitySelector(
            EntitySelectorConfig(domain="zone", multiple=True)
        ),
        vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=MAX_RADIUS_KM,
                step=0.5,
                unit_of_measurement="km",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_FUEL_TYPE, default=DEFAULT_FUEL_TYPE): SelectSelector(
            SelectSelectorConfig(
                options=FUEL_TYPES,
                translation_key=CONF_FUEL_TYPE,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_STATION_FILTER, default=""): TextSelector(
            TextSelectorConfig(multiline=True)
        ),
        vol.Optional(CONF_FIXED_TIMES, default=""): TextSelector(
            TextSelectorConfig(multiline=True)
        ),
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=5,
                max=1440,
                step=1,
                unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_MOVE_THRESHOLD, default=DEFAULT_MOVE_THRESHOLD): NumberSelector(
            NumberSelectorConfig(
                min=0.5,
                max=100,
                step=0.5,
                unit_of_measurement="km",
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
).extend(SETTINGS_SCHEMA.schema)


def _validate_settings(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not user_input[CONF_ENABLE_HOME] and not user_input[CONF_ENABLE_MOBILE]:
        errors["base"] = "no_location"
    elif user_input[CONF_ENABLE_MOBILE] and not user_input.get(CONF_TRACKER):
        errors[CONF_TRACKER] = "tracker_required"
    try:
        parse_fixed_times(user_input.get(CONF_FIXED_TIMES))
    except ValueError:
        errors[CONF_FIXED_TIMES] = "invalid_time"
    return errors


class TankerkoenigMobileConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for API key and settings, validate the key with a test request."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_settings(user_input)
            if not errors:
                try:
                    await async_get_stations(
                        async_get_clientsession(self.hass),
                        user_input[CONF_API_KEY],
                        self.hass.config.latitude,
                        self.hass.config.longitude,
                        1,
                        DEFAULT_FUEL_TYPE,
                    )
                except TankerkoenigAuthError:
                    errors["base"] = "invalid_auth"
                except TankerkoenigError:
                    errors["base"] = "cannot_connect"
                else:
                    options = {k: v for k, v in user_input.items() if k != CONF_API_KEY}
                    return self.async_create_entry(
                        title="Tankerkönig Mobile",
                        data={CONF_API_KEY: user_input[CONF_API_KEY]},
                        options=options,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or {CONF_PAUSE_ZONES: ["zone.home"]}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TankerkoenigMobileOptionsFlow:
        """Return the options flow."""
        return TankerkoenigMobileOptionsFlow()


class TankerkoenigMobileOptionsFlow(OptionsFlow):
    """Change all settings except the API key."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_settings(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                SETTINGS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )
