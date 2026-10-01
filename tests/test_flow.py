"""Assistant : zone mobile et favoris hors zone ; coordinateur : suivi du déplacement."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carburant_holm.const import DOMAIN


def rec(i, ville="Lille", lat=50.63, lon=3.06, cp="59000", prix=1.8):
    return {"id": i, "ville": ville, "cp": cp, "adresse": f"{i} rue X", "geom": {"lat": lat, "lon": lon}, "gazole_prix": prix, "gazole_maj": "2026-09-30T08:00:00+02:00"}


class FakeOsm:
    async def async_infos_quick(self, recs, zones, wait):
        return {str(r["id"]): {"name": "Super U", "brand": "Super U", "logo": None} for r in recs}


@pytest.fixture(autouse=True)
def fakes():
    zone = [rec(1), rec(2, lat=50.64)]
    with patch("custom_components.carburant_holm.api.FuelApi.stations_in_zone", AsyncMock(return_value=zone)) as z, \
         patch("custom_components.carburant_holm.api.FuelApi.stations_by_ids", AsyncMock(return_value=[])), \
         patch("custom_components.carburant_holm.api.FuelApi.stations_search", AsyncMock(return_value=[rec(77, "Poitiers", 46.58, 0.34, "86000", 1.7)])) as srch, \
         patch("custom_components.carburant_holm.config_flow.async_get_osm", AsyncMock(return_value=FakeOsm())), \
         patch("custom_components.carburant_holm.coordinator.async_get_osm", AsyncMock(return_value=FakeOsm())):
        yield z, srch


async def test_zone_mobile_et_favori_hors_zone(hass: HomeAssistant, fakes):
    hass.states.async_set("person.oliv", "not_home", {"latitude": 50.63, "longitude": 3.06, "friendly_name": "Oliv"})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tracker": "person.oliv"})
    assert r["step_id"] == "zone_mobile"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"radius_km": 5, "fuels": ["gazole"], "max_age_days": 7, "scan_interval": 30})
    assert r["step_id"] == "favorites"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"favorites": ["1"], "search": "Poitiers"})
    assert r["step_id"] == "favorites"
    labels = {o["value"]: o["label"] for o in r["data_schema"].schema["favorites"].config["options"]}
    assert "hors zone" in labels["77"]
    assert "(1 Rue X)" in labels["1"]  # homonymes : adresse ajoutée
    add = r["data_schema"].schema["add"].config["options"]  # résultats visibles en cases à cocher
    assert [o["value"] for o in add] == ["77"] and "Poitiers" in r["description_placeholders"]["info"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"favorites": ["1"], "add": ["77"]})
    assert r["type"] == "create_entry"
    opts = r["options"]
    assert opts["tracker"] == "person.oliv" and opts["favorites"] == ["1", "77"] and opts["radius_km"] == 5
    assert opts["zone_name"] == "autour de Oliv"


async def test_coordinateur_suit_le_deplacement(hass: HomeAssistant, fakes):
    zone_mock, _ = fakes
    hass.states.async_set("person.oliv", "not_home", {"latitude": 50.63, "longitude": 3.06})
    entry = MockConfigEntry(domain=DOMAIN, title="Carburant autour de Oliv", data={}, options={
        "zone_name": "autour de Oliv", "latitude": 48.0, "longitude": 2.0, "radius_km": 3, "fuels": ["gazole"], "tracker": "person.oliv"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert (coord.lat, coord.lon) == (50.63, 3.06)  # centre = position de la personne
    assert coord.history["zone"] == {}  # pas d'historique de zone pour une zone mobile
    n = zone_mock.await_count
    hass.states.async_set("person.oliv", "not_home", {"latitude": 50.70, "longitude": 3.06})  # ~7,8 km
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    assert zone_mock.await_count > n
    assert round(coord.lat, 2) == 50.70
