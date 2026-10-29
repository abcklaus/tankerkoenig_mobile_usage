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
from homeassistant.data_entry_flow import section
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
    CONF_DWELL_MINUTES,
    CONF_ENABLE_HOME,
    CONF_ENABLE_MOBILE,
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
    MAX_RADIUS_KM,
    SECTION_HOME,
    SECTION_MOBILE,
)
from .coordinator import parse_fixed_times


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


HOME_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENABLE_HOME, default=True): BooleanSelector(),
        vol.Optional(CONF_STATION_FILTER, default=""): TextSelector(
            TextSelectorConfig(multiline=True)
        ),
    }
)

MOBILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENABLE_MOBILE, default=True): BooleanSelector(),
        vol.Optional(CONF_TRACKER): EntitySelector(
            EntitySelectorConfig(domain=["device_tracker", "person", "zone"])
        ),
        # No default: the field must stay clearable in the options flow.
        vol.Optional(CONF_PAUSE_ZONES): EntitySelector(
            EntitySelectorConfig(domain="zone", multiple=True)
        ),
        vol.Required(CONF_DWELL_MINUTES, default=DEFAULT_DWELL_MINUTES): _number(
            1, 240, 1, "min"
        ),
        vol.Required(
            CONF_STATIONARY_TOLERANCE, default=DEFAULT_STATIONARY_TOLERANCE
        ): _number(10, 5000, 10, "m"),
    }
)

SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FUEL_TYPE, default=DEFAULT_FUEL_TYPE): SelectSelector(
            SelectSelectorConfig(
                options=FUEL_TYPES,
                translation_key=CONF_FUEL_TYPE,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS): _number(1, MAX_RADIUS_KM, 0.5, "km"),
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _number(
            5, 1440, 1, "min"
        ),
        vol.Optional(CONF_FIXED_TIMES, default=""): TextSelector(
            TextSelectorConfig(multiline=True)
        ),
        vol.Required(SECTION_HOME): section(HOME_SCHEMA, {"collapsed": False}),
        vol.Required(SECTION_MOBILE): section(MOBILE_SCHEMA, {"collapsed": False}),
    }
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
).extend(SETTINGS_SCHEMA.schema)


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Merge the section dicts into one flat dict (options are stored flat)."""
    flat = {k: v for k, v in user_input.items() if k not in (SECTION_HOME, SECTION_MOBILE)}
    flat.update(user_input.get(SECTION_HOME, {}))
    flat.update(user_input.get(SECTION_MOBILE, {}))
    return flat


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    """Split flat options into the section layout of the form."""
    home_keys = {str(key) for key in HOME_SCHEMA.schema}
    mobile_keys = {str(key) for key in MOBILE_SCHEMA.schema}
    nested: dict[str, Any] = {SECTION_HOME: {}, SECTION_MOBILE: {}}
    for key, value in flat.items():
        if key in home_keys:
            nested[SECTION_HOME][key] = value
        elif key in mobile_keys:
            nested[SECTION_MOBILE][key] = value
        else:
            nested[key] = value
    return nested


def _validate_settings(settings: dict[str, Any]) -> dict[str, str]:
    """Validate flat settings; errors are reported as base errors."""
    errors: dict[str, str] = {}
    if not settings[CONF_ENABLE_HOME] and not settings[CONF_ENABLE_MOBILE]:
        errors["base"] = "no_location"
    elif settings[CONF_ENABLE_MOBILE] and not settings.get(CONF_TRACKER):
        errors["base"] = "tracker_required"
    else:
        try:
            parse_fixed_times(settings.get(CONF_FIXED_TIMES))
        except ValueError:
            errors["base"] = "invalid_time"
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
            settings = _flatten(user_input)
            errors = _validate_settings(settings)
            if not errors:
                try:
                    await async_get_stations(
                        async_get_clientsession(self.hass),
                        settings[CONF_API_KEY],
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
                    api_key = settings.pop(CONF_API_KEY)
                    return self.async_create_entry(
                        title="Tankerkönig Mobile",
                        data={CONF_API_KEY: api_key},
                        options=settings,
                    )

        suggested = user_input or {SECTION_MOBILE: {CONF_PAUSE_ZONES: ["zone.home"]}}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, suggested),
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
            settings = _flatten(user_input)
            errors = _validate_settings(settings)
            if not errors:
                return self.async_create_entry(data=settings)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                SETTINGS_SCHEMA, user_input or _nest(dict(self.config_entry.options))
            ),
            errors=errors,
        )
