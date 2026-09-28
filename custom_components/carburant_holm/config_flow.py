"""Assistant de configuration Carburant HOLM."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import FuelApi, FuelApiError, parse_station
from .names import async_get_station_names
from .const import (
    CONF_CITY,
    CONF_FAVORITES,
    CONF_FUELS,
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_MAX_AGE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_ZONE_NAME,
    DEFAULT_FUELS,
    DEFAULT_MAX_AGE,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FUELS,
)

_LOGGER = logging.getLogger(__name__)


class _Common:
    """Étapes partagées entre la création et les options."""

    hass: Any
    _data: dict[str, Any]
    _communes: list[dict]

    def _api(self) -> FuelApi:
        return FuelApi(async_get_clientsession(self.hass))

    async def _names(self) -> dict:
        return await async_get_station_names(self.hass)

    async def _search_city(self, city: str) -> str | None:
        """Retourne une clé d'erreur ou None ; remplit self._communes."""
        try:
            self._communes = await self._api().search_communes(city)
        except FuelApiError:
            return "cannot_connect"
        if not self._communes:
            return "city_not_found"
        return None

    async def async_step_commune(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            c = next((x for x in self._communes if x["code"] == user_input["commune"]), self._communes[0])
            self._data[CONF_LATITUDE] = c["latitude"]
            self._data[CONF_LONGITUDE] = c["longitude"]
            if not self._data.get(CONF_ZONE_NAME) or self._data.get("_auto_name"):
                self._data[CONF_ZONE_NAME] = c["name"]
            return await self.async_step_zone()
        if len(self._communes) == 1:
            return await self.async_step_commune({"commune": self._communes[0]["code"]})
        options = [SelectOptionDict(value=c["code"], label=c["label"]) for c in self._communes]
        return self.async_show_form(
            step_id="commune",
            data_schema=vol.Schema({vol.Required("commune", default=options[0]["value"]): SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))}),
        )

    async def async_step_zone(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            loc = user_input.get(CONF_LOCATION) or {}
            radius_km = round(float(loc.get("radius") or DEFAULT_RADIUS * 1000) / 1000, 1)
            if not 1 <= radius_km <= 50:
                errors["base"] = "radius_range"
            elif not user_input.get(CONF_FUELS):
                errors["base"] = "no_fuel"
            else:
                self._data.update({
                    CONF_LATITUDE: loc.get("latitude", self._data.get(CONF_LATITUDE)),
                    CONF_LONGITUDE: loc.get("longitude", self._data.get(CONF_LONGITUDE)),
                    CONF_RADIUS: radius_km,
                    CONF_FUELS: user_input[CONF_FUELS],
                    CONF_MAX_AGE: int(user_input.get(CONF_MAX_AGE, DEFAULT_MAX_AGE)),
                    CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                })
                return await self.async_step_favorites()
        d = self._data
        fuel_opts = [SelectOptionDict(value=k, label=v) for k, v in FUELS.items()]
        schema = vol.Schema({
            vol.Required(CONF_LOCATION, default={
                "latitude": d.get(CONF_LATITUDE, self.hass.config.latitude),
                "longitude": d.get(CONF_LONGITUDE, self.hass.config.longitude),
                "radius": float(d.get(CONF_RADIUS, DEFAULT_RADIUS)) * 1000,
            }): LocationSelector(LocationSelectorConfig(radius=True, icon="mdi:gas-station")),
            vol.Required(CONF_FUELS, default=d.get(CONF_FUELS, DEFAULT_FUELS)): SelectSelector(SelectSelectorConfig(options=fuel_opts, multiple=True, mode=SelectSelectorMode.LIST)),
            vol.Required(CONF_MAX_AGE, default=d.get(CONF_MAX_AGE, DEFAULT_MAX_AGE)): NumberSelector(NumberSelectorConfig(min=1, max=30, step=1, unit_of_measurement="jours", mode=NumberSelectorMode.BOX)),
            vol.Required(CONF_SCAN_INTERVAL, default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): NumberSelector(NumberSelectorConfig(min=10, max=1440, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX)),
        })
        return self.async_show_form(step_id="zone", data_schema=schema, errors=errors, description_placeholders={"zone": d.get(CONF_ZONE_NAME, "")})

    async def async_step_favorites(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_FAVORITES] = user_input.get(CONF_FAVORITES, [])
            return self._finish()
        d = self._data
        center = (float(d[CONF_LATITUDE]), float(d[CONF_LONGITUDE]))
        current = [str(s) for s in d.get(CONF_FAVORITES, [])]
        errors: dict[str, str] = {}
        stations: list[dict] = []
        try:
            api = self._api()
            names = await self._names()
            raw = await api.stations_in_zone(center[0], center[1], d[CONF_RADIUS])
            ids = {str(r.get("id")) for r in raw}
            missing = [s for s in current if s not in ids]
            if missing:
                raw += await api.stations_by_ids(missing)
            stations = [s for s in (parse_station(r, names, center) for r in raw) if s]
        except FuelApiError:
            errors["base"] = "cannot_connect"
        stations.sort(key=lambda s: (s["distance"] if s["distance"] is not None else 999))
        fuels = d.get(CONF_FUELS, DEFAULT_FUELS)

        def label(s: dict) -> str:
            nm = s["name"] if (not s["brand"] or s["brand"].lower() in s["name"].lower()) else f"{s['brand']} · {s['name']}"
            prices = " · ".join(f"{FUELS[f]} {s['fuels'][f]['price']:.3f}".replace(".", ",") for f in fuels if s["fuels"].get(f, {}).get("price") is not None)
            dist = f"{s['distance']:.1f} km".replace(".", ",") if s["distance"] is not None else ""
            return f"{nm} — {s['city']} {dist}" + (f"  ({prices})" if prices else "")

        options = [SelectOptionDict(value=s["id"], label=label(s)) for s in stations]
        known = {o["value"] for o in options}
        options += [SelectOptionDict(value=sid, label=f"Station {sid}") for sid in current if sid not in known]
        schema = vol.Schema({
            vol.Optional(CONF_FAVORITES, default=current): SelectSelector(SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN)),
        })
        return self.async_show_form(step_id="favorites", data_schema=schema, errors=errors, description_placeholders={"count": str(len(stations)), "radius": f"{d[CONF_RADIUS]:g}"})

    def _finish(self) -> ConfigFlowResult:  # pragma: no cover - surchargé
        raise NotImplementedError


class CarburantHolmConfigFlow(_Common, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._communes: list[dict] = []

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = (user_input.get(CONF_ZONE_NAME) or "").strip()
            city = (user_input.get(CONF_CITY) or "").strip()
            self._data[CONF_ZONE_NAME] = name or "Maison"
            self._data["_auto_name"] = not name
            if city:
                err = await self._search_city(city)
                if err:
                    errors[CONF_CITY] = err
                else:
                    return await self.async_step_commune()
            else:
                self._data[CONF_LATITUDE] = self.hass.config.latitude
                self._data[CONF_LONGITUDE] = self.hass.config.longitude
                return await self.async_step_zone()
        schema = vol.Schema({
            vol.Optional(CONF_ZONE_NAME): TextSelector(),
            vol.Optional(CONF_CITY): TextSelector(),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    def _finish(self) -> ConfigFlowResult:
        data = {k: v for k, v in self._data.items() if not k.startswith("_")}
        return self.async_create_entry(title=f"Carburant {data[CONF_ZONE_NAME]}", data={}, options=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CarburantHolmOptionsFlow()


class CarburantHolmOptionsFlow(_Common, OptionsFlow):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._communes: list[dict] = []

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if not self._data:
            self._data = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_ZONE_NAME):
                self._data[CONF_ZONE_NAME] = user_input[CONF_ZONE_NAME].strip()
            city = (user_input.get(CONF_CITY) or "").strip()
            if city:
                err = await self._search_city(city)
                if err:
                    errors[CONF_CITY] = err
                else:
                    return await self.async_step_commune()
            else:
                return await self.async_step_zone()
        schema = vol.Schema({
            vol.Optional(CONF_ZONE_NAME, default=self._data.get(CONF_ZONE_NAME, "")): TextSelector(),
            vol.Optional(CONF_CITY): TextSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _finish(self) -> ConfigFlowResult:
        data = {k: v for k, v in self._data.items() if not k.startswith("_")}
        title = f"Carburant {data.get(CONF_ZONE_NAME) or 'Maison'}"
        if title != self.config_entry.title:
            self.hass.config_entries.async_update_entry(self.config_entry, title=title)
        return self.async_create_entry(title="", data=data)
