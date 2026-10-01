"""Assistant de configuration Carburant HOLM."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
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
from .coordinator import tracker_position
from .osm import async_get_osm
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
    CONF_SEARCH,
    CONF_TRACKER,
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
        # dans l'assistant, on répond vite (un proxy coupe souvent au-delà de 60 s)
        return FuelApi(async_get_clientsession(self.hass), retries=2, timeout=15)

    async def _search_city(self, city: str) -> str | None:
        """Retourne une clé d'erreur ou None ; remplit self._communes."""
        try:
            self._communes = await self._api().search_communes(city)
        except FuelApiError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Erreur inattendue en cherchant la commune")
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
        mobile = bool(self._data.get(CONF_TRACKER))
        if user_input is not None and mobile:
            pos = tracker_position(self.hass, self._data[CONF_TRACKER]) or (self.hass.config.latitude, self.hass.config.longitude)
            user_input = {**user_input, CONF_LOCATION: {"latitude": pos[0], "longitude": pos[1], "radius": float(user_input.get(CONF_RADIUS, DEFAULT_RADIUS)) * 1000}}
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
        where = vol.Required(CONF_RADIUS, default=float(d.get(CONF_RADIUS, DEFAULT_RADIUS))) if mobile else vol.Required(CONF_LOCATION, default={
                "latitude": d.get(CONF_LATITUDE, self.hass.config.latitude),
                "longitude": d.get(CONF_LONGITUDE, self.hass.config.longitude),
                "radius": float(d.get(CONF_RADIUS, DEFAULT_RADIUS)) * 1000,
            })
        where_sel = NumberSelector(NumberSelectorConfig(min=1, max=50, step=1, unit_of_measurement="km", mode=NumberSelectorMode.SLIDER)) if mobile else LocationSelector(LocationSelectorConfig(radius=True, icon="mdi:gas-station"))
        schema = vol.Schema({
            where: where_sel,
            vol.Required(CONF_FUELS, default=d.get(CONF_FUELS, DEFAULT_FUELS)): SelectSelector(SelectSelectorConfig(options=fuel_opts, multiple=True, mode=SelectSelectorMode.LIST)),
            vol.Required(CONF_MAX_AGE, default=d.get(CONF_MAX_AGE, DEFAULT_MAX_AGE)): NumberSelector(NumberSelectorConfig(min=1, max=30, step=1, unit_of_measurement="jours", mode=NumberSelectorMode.BOX)),
            vol.Required(CONF_SCAN_INTERVAL, default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): NumberSelector(NumberSelectorConfig(min=10, max=1440, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX)),
        })
        tname = ""
        if mobile:
            st = self.hass.states.get(self._data[CONF_TRACKER])
            tname = st.name if st else self._data[CONF_TRACKER]
        return self.async_show_form(step_id="zone_mobile" if mobile else "zone", data_schema=schema, errors=errors,
                                    description_placeholders={"zone": d.get(CONF_ZONE_NAME, ""), "tracker": tname})

    async def async_step_zone_mobile(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self.async_step_zone(user_input)

    async def async_step_favorites(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        d = self._data
        if user_input is not None:
            favs = [str(x) for x in user_input.get(CONF_FAVORITES, [])]
            favs += [str(x) for x in user_input.get("add", []) if str(x) not in favs]  # résultats de recherche cochés
            d[CONF_FAVORITES] = favs
            self._results = []
            query = (user_input.get(CONF_SEARCH) or "").strip()
            if not query:
                return self._finish()
            try:
                found = await self._api().stations_search(query)
            except FuelApiError:
                found, errors["base"] = [], "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue en cherchant des stations")
                found, errors["base"] = [], "cannot_connect"
            if not found and not errors:
                errors[CONF_SEARCH] = "no_station"
            known = {str(r.get("id")) for r in getattr(self, "_found", [])}
            self._found = getattr(self, "_found", []) + [r for r in found if str(r.get("id")) not in known]
            self._results = [str(r.get("id")) for r in found if str(r.get("id")) not in favs]
            self._last_search = query
        center = (float(d[CONF_LATITUDE]), float(d[CONF_LONGITUDE]))
        current = [str(s) for s in d.get(CONF_FAVORITES, [])]
        stations: list[dict] = []
        try:
            api = self._api()
            raw = await api.stations_in_zone(center[0], center[1], d[CONF_RADIUS])
            ids = {str(r.get("id")) for r in raw}
            in_zone = set(ids)
            extra = [r for r in getattr(self, "_found", []) if str(r.get("id")) not in ids]
            raw += extra
            ids |= {str(r.get("id")) for r in extra}
            missing = [s for s in current if s not in ids]
            if missing:
                raw += await api.stations_by_ids(missing)
            try:
                osm = await async_get_osm(self.hass)
                infos = await osm.async_infos_quick(raw, [(center[0], center[1], float(d[CONF_RADIUS]))], wait=10)
            except Exception as err:  # noqa: BLE001 - les noms ne doivent jamais bloquer l'assistant
                _LOGGER.warning("Noms des stations indisponibles : %s", err)
                infos = {}
            stations = [s for s in (parse_station(r, infos, center) for r in raw) if s]
            for st in stations:
                st["in_zone"] = st["id"] in in_zone
        except FuelApiError as err:
            _LOGGER.warning("Stations de la zone indisponibles : %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Erreur inattendue en listant les stations")
            errors["base"] = "cannot_connect"
        # stations de la zone d'abord (par distance), puis celles trouvées ailleurs
        stations.sort(key=lambda s: (not s.get("in_zone", True), s["distance"] if s["distance"] is not None else 999))
        fuels = d.get(CONF_FUELS, DEFAULT_FUELS)
        names: dict[str, int] = {}
        for st in stations:
            names[f"{st['name']}|{st['city']}"] = names.get(f"{st['name']}|{st['city']}", 0) + 1

        def label(s: dict) -> str:
            nm = s["name"] if (not s["brand"] or s["brand"].lower() in s["name"].lower()) else f"{s['brand']} · {s['name']}"
            if names.get(f"{s['name']}|{s['city']}", 0) > 1 and s.get("address"):
                nm = f"{nm} ({s['address']})"
            prices = " · ".join(f"{FUELS[f]} {s['fuels'][f]['price']:.3f}".replace(".", ",") for f in fuels if s["fuels"].get(f, {}).get("price") is not None)
            dist = f"{s['distance']:.1f} km".replace(".", ",") if s["distance"] is not None else ""
            where = f"{s['city']} {dist}" + ("" if s.get("in_zone", True) else " · hors zone")
            return f"{nm} — {where}" + (f"  ({prices})" if prices else "")

        options = [SelectOptionDict(value=s["id"], label=label(s)) for s in stations]
        known = {o["value"] for o in options}
        options += [SelectOptionDict(value=sid, label=f"Station {sid}") for sid in current if sid not in known]
        fields: dict = {vol.Optional(CONF_FAVORITES, default=current): SelectSelector(SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN))}
        # résultats de la dernière recherche : cases à cocher bien visibles
        results = [s for s in stations if s["id"] in getattr(self, "_results", [])]
        if results:
            fields[vol.Optional("add", default=[s["id"] for s in results] if len(results) == 1 else [])] = SelectSelector(
                SelectSelectorConfig(options=[SelectOptionDict(value=s["id"], label=label(s)) for s in results], multiple=True, mode=SelectSelectorMode.LIST))
        fields[vol.Optional(CONF_SEARCH)] = TextSelector()
        n_zone = sum(1 for s in stations if s.get("in_zone", True))
        last = getattr(self, "_last_search", "")
        info = (f"{len(results)} station(s) trouvée(s) pour « {last} » : coche celles à ajouter puis valide." if results
                else f"Aucune nouvelle station pour « {last} »." if last and not errors else "")
        return self.async_show_form(step_id="favorites", data_schema=vol.Schema(fields), errors=errors,
                                    description_placeholders={"count": str(n_zone), "radius": f"{d[CONF_RADIUS]:g}",
                                                              "found": str(len(stations) - n_zone), "info": info})

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
            tracker = user_input.get(CONF_TRACKER)
            self._data[CONF_ZONE_NAME] = name or "Maison"
            self._data["_auto_name"] = not name
            if tracker:
                st = self.hass.states.get(tracker)
                pos = tracker_position(self.hass, tracker)
                if st is None:
                    errors[CONF_TRACKER] = "tracker_unknown"
                else:
                    self._data[CONF_TRACKER] = tracker
                    if not name:
                        self._data[CONF_ZONE_NAME] = f"autour de {st.name}"
                    self._data[CONF_LATITUDE], self._data[CONF_LONGITUDE] = pos or (self.hass.config.latitude, self.hass.config.longitude)
                    return await self.async_step_zone()
            elif city:
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
            vol.Optional(CONF_TRACKER): EntitySelector(EntitySelectorConfig(domain=["person", "device_tracker"])),
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
            tracker = user_input.get(CONF_TRACKER)
            if tracker:
                self._data[CONF_TRACKER] = tracker
                pos = tracker_position(self.hass, tracker)
                if pos:
                    self._data[CONF_LATITUDE], self._data[CONF_LONGITUDE] = pos
                return await self.async_step_zone()
            self._data.pop(CONF_TRACKER, None)
            if city:
                err = await self._search_city(city)
                if err:
                    errors[CONF_CITY] = err
                else:
                    return await self.async_step_commune()
            else:
                return await self.async_step_zone()
        tr = self._data.get(CONF_TRACKER)
        schema = vol.Schema({
            vol.Optional(CONF_ZONE_NAME, default=self._data.get(CONF_ZONE_NAME, "")): TextSelector(),
            vol.Optional(CONF_CITY): TextSelector(),
            (vol.Optional(CONF_TRACKER, description={"suggested_value": tr}) if tr else vol.Optional(CONF_TRACKER)): EntitySelector(EntitySelectorConfig(domain=["person", "device_tracker"])),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _finish(self) -> ConfigFlowResult:
        data = {k: v for k, v in self._data.items() if not k.startswith("_")}
        title = f"Carburant {data.get(CONF_ZONE_NAME) or 'Maison'}"
        if title != self.config_entry.title:
            self.hass.config_entries.async_update_entry(self.config_entry, title=title)
        return self.async_create_entry(title="", data=data)
