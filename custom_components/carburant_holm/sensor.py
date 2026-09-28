"""Capteurs Carburant HOLM."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, FUELS
from .coordinator import FuelCoordinator

UNIT = "€/L"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coord: FuelCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for fuel in coord.fuels:
        entities.append(BestPriceSensor(coord, fuel))
        entities.append(AveragePriceSensor(coord, fuel))
    stations = (coord.data or {}).get("stations", {})
    for sid in coord.favorites:
        st = stations.get(sid)
        for fuel in coord.fuels:
            if st is None or fuel in st["fuels"]:
                entities.append(StationFuelSensor(coord, sid, fuel))
    async_add_entities(entities)


def _zone_device(coord: FuelCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"zone_{coord.config_entry.entry_id}")},
        name=f"Carburant {coord.zone_name}",
        manufacturer="HOLM",
        model=f"Zone {coord.radius:g} km",
        entry_type=DeviceEntryType.SERVICE,
    )


def _days_since(value: str | None) -> int | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return (dt_util.now() - d).days


class _Base(CoordinatorEntity[FuelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, coord: FuelCoordinator, fuel: str) -> None:
        super().__init__(coord)
        self.fuel = fuel

    @property
    def _stats(self) -> dict:
        return (self.coordinator.data or {}).get("stats", {}).get(self.fuel) or {}

    @property
    def _stations(self) -> dict:
        return (self.coordinator.data or {}).get("stations", {})


class BestPriceSensor(_Base):
    """Prix le plus bas de la zone pour un carburant."""

    _attr_icon = "mdi:gas-station"
    _unrecorded_attributes = frozenset({"top", "latitude", "longitude", "address"})

    def __init__(self, coord: FuelCoordinator, fuel: str) -> None:
        super().__init__(coord, fuel)
        self._attr_unique_id = f"{coord.config_entry.entry_id}_best_{fuel}"
        self._attr_name = f"Meilleur prix {FUELS[fuel]}"
        self._attr_device_info = _zone_device(coord)

    @property
    def native_value(self) -> float | None:
        return self._stats.get("best_price")

    @property
    def entity_picture(self) -> str | None:
        st = self._stations.get(self._stats.get("best") or "")
        return st.get("logo") if st else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._stats
        st = self._stations.get(s.get("best") or "") or {}
        f = (st.get("fuels") or {}).get(self.fuel) or {}
        return {
            "fuel": FUELS[self.fuel],
            "station_id": st.get("id"),
            "station": st.get("name"),
            "brand": st.get("brand"),
            "address": st.get("address"),
            "city": st.get("city"),
            "distance_km": st.get("distance"),
            "latitude": st.get("latitude"),
            "longitude": st.get("longitude"),
            "updated": f.get("updated"),
            "average": s.get("average"),
            "max": s.get("max"),
            "stations_count": s.get("count"),
            "trend_1d": s.get("trend_1d"),
            "trend_7d": s.get("trend_7d"),
            "top": self.coordinator.top(self.fuel),
        }


class AveragePriceSensor(_Base):
    """Prix moyen de la zone."""

    _attr_icon = "mdi:scale-balance"

    def __init__(self, coord: FuelCoordinator, fuel: str) -> None:
        super().__init__(coord, fuel)
        self._attr_unique_id = f"{coord.config_entry.entry_id}_avg_{fuel}"
        self._attr_name = f"Prix moyen {FUELS[fuel]}"
        self._attr_device_info = _zone_device(coord)

    @property
    def native_value(self) -> float | None:
        return self._stats.get("average")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fuel": FUELS[self.fuel], "stations_count": self._stats.get("count"), "max": self._stats.get("max")}


class StationFuelSensor(_Base):
    """Prix d'un carburant dans une station favorite."""

    _attr_icon = "mdi:gas-station-outline"

    def __init__(self, coord: FuelCoordinator, sid: str, fuel: str) -> None:
        super().__init__(coord, fuel)
        self.sid = sid
        self._attr_unique_id = f"{coord.config_entry.entry_id}_st_{sid}_{fuel}"
        self._attr_name = FUELS[fuel]
        st = (coord.data or {}).get("stations", {}).get(sid) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"station_{sid}")},
            name=st.get("name") or f"Station {sid}",
            manufacturer=st.get("brand") or "Station",
            model=f"{st.get('address', '')} {st.get('city', '')}".strip() or None,
            entry_type=DeviceEntryType.SERVICE,
            via_device=(DOMAIN, f"zone_{coord.config_entry.entry_id}"),
        )

    @property
    def _st(self) -> dict:
        return self._stations.get(self.sid) or {}

    @property
    def _f(self) -> dict:
        return (self._st.get("fuels") or {}).get(self.fuel) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._st)

    @property
    def native_value(self) -> float | None:
        return self._f.get("price")

    @property
    def entity_picture(self) -> str | None:
        return self._st.get("logo")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st, f, s = self._st, self._f, self._stats
        best = s.get("best_price")
        price = f.get("price")
        return {
            "fuel": FUELS[self.fuel],
            "station_id": self.sid,
            "station": st.get("name"),
            "brand": st.get("brand"),
            "address": st.get("address"),
            "city": st.get("city"),
            "distance_km": st.get("distance"),
            "updated": f.get("updated"),
            "days_since_update": _days_since(f.get("updated")),
            "shortage": f.get("shortage"),
            "rank": f.get("rank"),
            "stations_count": s.get("count"),
            "delta_vs_best": round(price - best, 3) if price is not None and best is not None else None,
            "delta_vs_average": round(price - s["average"], 3) if price is not None and s.get("average") is not None else None,
        }
