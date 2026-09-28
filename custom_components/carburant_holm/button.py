"""Bouton d'actualisation."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FuelCoordinator
from .sensor import _zone_device


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(CoordinatorEntity[FuelCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Actualiser les prix"
    _attr_icon = "mdi:refresh"

    def __init__(self, coord: FuelCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.config_entry.entry_id}_refresh"
        self._attr_device_info = _zone_device(coord)

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
