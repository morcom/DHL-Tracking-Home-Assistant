"""Config flow for DHL Parcel NL integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ACCOUNT_TYPE_BUSINESS,
    ACCOUNT_TYPE_CONSUMER,
    CONF_API_KEY,
    CONF_ACCOUNT_TYPE,
    CONF_DELIVERED_KEEP_DAYS,
    CONF_EMAIL,
    CONF_EMAIL_ENCRYPTED,
    CONF_CREDENTIALS_ENCRYPTED,
    CONF_PASSWORD,
    CONF_PASSWORD_ENCRYPTED,
    CONF_POSTAL_CODE,
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_SUMMARY_LANGUAGE,
    CONF_REFRESH_START_TIME,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_SUMMARY_LANGUAGE,
    DEFAULT_REFRESH_START_TIME,
    DEFAULT_DELIVERED_KEEP_DAYS,
    DOMAIN,
)
from .crypto import encrypt_text


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_ACCOUNT_TYPE,
            default=ACCOUNT_TYPE_CONSUMER,
        ): vol.In([ACCOUNT_TYPE_CONSUMER, ACCOUNT_TYPE_BUSINESS]),
        vol.Optional(CONF_EMAIL): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_USER_ID): str,
        vol.Optional(CONF_API_KEY): str,
        vol.Optional(CONF_POSTAL_CODE): str,
        vol.Optional(
            CONF_DELIVERED_KEEP_DAYS,
            default=DEFAULT_DELIVERED_KEEP_DAYS,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        vol.Optional(
            CONF_REFRESH_INTERVAL_MINUTES,
            default=DEFAULT_SCAN_INTERVAL_MINUTES,
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional(
            CONF_REFRESH_START_TIME,
            default=DEFAULT_REFRESH_START_TIME,
        ): str,
        vol.Optional(
            CONF_REFRESH_END_TIME,
            default=DEFAULT_REFRESH_END_TIME,
        ): str,
        vol.Optional(
            CONF_SUMMARY_LANGUAGE,
            default=DEFAULT_SUMMARY_LANGUAGE,
        ): vol.In(["en", "pl", "nl"]),
    }
)


class DHLParcelNLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DHL Parcel NL."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            account_type = user_input.get(CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER)
            has_consumer = bool(
                user_input.get(CONF_EMAIL) and user_input.get(CONF_PASSWORD)
            )
            has_business = bool(
                user_input.get(CONF_USER_ID) and user_input.get(CONF_API_KEY)
            )

            if account_type == ACCOUNT_TYPE_CONSUMER and not has_consumer:
                errors["base"] = "invalid_auth_consumer"
            elif account_type == ACCOUNT_TYPE_BUSINESS and not has_business:
                errors["base"] = "invalid_auth_business"
            elif not has_consumer and not has_business:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                if CONF_DELIVERED_KEEP_DAYS not in user_input:
                    user_input[CONF_DELIVERED_KEEP_DAYS] = DEFAULT_DELIVERED_KEEP_DAYS
                if CONF_REFRESH_INTERVAL_MINUTES not in user_input:
                    user_input[CONF_REFRESH_INTERVAL_MINUTES] = (
                        DEFAULT_SCAN_INTERVAL_MINUTES
                    )
                if CONF_REFRESH_START_TIME not in user_input:
                    user_input[CONF_REFRESH_START_TIME] = DEFAULT_REFRESH_START_TIME
                if CONF_REFRESH_END_TIME not in user_input:
                    user_input[CONF_REFRESH_END_TIME] = DEFAULT_REFRESH_END_TIME
                if CONF_SUMMARY_LANGUAGE not in user_input:
                    user_input[CONF_SUMMARY_LANGUAGE] = DEFAULT_SUMMARY_LANGUAGE

                if account_type == ACCOUNT_TYPE_CONSUMER:
                    secure_input = dict(user_input)
                    secure_input[CONF_EMAIL_ENCRYPTED] = encrypt_text(
                        self.hass, user_input.get(CONF_EMAIL, "")
                    )
                    secure_input[CONF_PASSWORD_ENCRYPTED] = encrypt_text(
                        self.hass, user_input.get(CONF_PASSWORD, "")
                    )
                    secure_input[CONF_CREDENTIALS_ENCRYPTED] = True
                    secure_input.pop(CONF_EMAIL, None)
                    secure_input.pop(CONF_PASSWORD, None)
                    user_input = secure_input

                return self.async_create_entry(
                    title="DHL Parcel Netherlands",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get options flow."""
        return DHLParcelNLOptionsFlow(config_entry)


class DHLParcelNLOptionsFlow(OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            account_type = user_input.get(
                CONF_ACCOUNT_TYPE,
                self._config_entry.options.get(
                    CONF_ACCOUNT_TYPE,
                    self._config_entry.data.get(
                        CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER
                    ),
                ),
            )

            if account_type == ACCOUNT_TYPE_CONSUMER:
                current = {**self._config_entry.data, **self._config_entry.options}
                new_email = (user_input.get(CONF_EMAIL) or "").strip()
                new_password = user_input.get(CONF_PASSWORD) or ""

                if new_email:
                    user_input[CONF_EMAIL_ENCRYPTED] = encrypt_text(
                        self.hass, new_email
                    )
                elif current.get(CONF_EMAIL_ENCRYPTED):
                    user_input[CONF_EMAIL_ENCRYPTED] = current[CONF_EMAIL_ENCRYPTED]

                if new_password:
                    user_input[CONF_PASSWORD_ENCRYPTED] = encrypt_text(
                        self.hass, new_password
                    )
                elif current.get(CONF_PASSWORD_ENCRYPTED):
                    user_input[CONF_PASSWORD_ENCRYPTED] = current[
                        CONF_PASSWORD_ENCRYPTED
                    ]

                user_input[CONF_CREDENTIALS_ENCRYPTED] = True
                user_input.pop(CONF_EMAIL, None)
                user_input.pop(CONF_PASSWORD, None)

            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        data = self._config_entry.data

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ACCOUNT_TYPE,
                    default=options.get(
                        CONF_ACCOUNT_TYPE,
                        data.get(CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER),
                    ),
                ): vol.In([ACCOUNT_TYPE_CONSUMER, ACCOUNT_TYPE_BUSINESS]),
                vol.Optional(
                    CONF_EMAIL,
                    default="",
                ): str,
                vol.Optional(
                    CONF_PASSWORD,
                    default="",
                ): str,
                vol.Optional(
                    CONF_USER_ID,
                    default=options.get(CONF_USER_ID, data.get(CONF_USER_ID, "")),
                ): str,
                vol.Optional(
                    CONF_API_KEY,
                    default=options.get(CONF_API_KEY, data.get(CONF_API_KEY, "")),
                ): str,
                vol.Optional(
                    CONF_POSTAL_CODE,
                    default=options.get(
                        CONF_POSTAL_CODE, data.get(CONF_POSTAL_CODE, "")
                    ),
                ): str,
                vol.Optional(
                    "tracking_codes",
                    default=options.get("tracking_codes", ""),
                ): str,
                vol.Optional(
                    CONF_DELIVERED_KEEP_DAYS,
                    default=options.get(
                        CONF_DELIVERED_KEEP_DAYS,
                        data.get(CONF_DELIVERED_KEEP_DAYS, DEFAULT_DELIVERED_KEEP_DAYS),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_REFRESH_INTERVAL_MINUTES,
                    default=options.get(
                        CONF_REFRESH_INTERVAL_MINUTES,
                        data.get(
                            CONF_REFRESH_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Optional(
                    CONF_REFRESH_START_TIME,
                    default=options.get(
                        CONF_REFRESH_START_TIME,
                        data.get(CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME),
                    ),
                ): str,
                vol.Optional(
                    CONF_REFRESH_END_TIME,
                    default=options.get(
                        CONF_REFRESH_END_TIME,
                        data.get(CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME),
                    ),
                ): str,
                vol.Optional(
                    CONF_SUMMARY_LANGUAGE,
                    default=options.get(
                        CONF_SUMMARY_LANGUAGE,
                        data.get(CONF_SUMMARY_LANGUAGE, DEFAULT_SUMMARY_LANGUAGE),
                    ),
                ): vol.In(["en", "pl", "nl"]),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
