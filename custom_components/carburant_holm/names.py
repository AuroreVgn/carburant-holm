"""Noms et enseignes des stations (le flux gouvernemental ne les fournit pas).

Ordre de priorité :
1. cache local (.storage) rafraîchi chaque semaine depuis la liste communautaire
   maintenue par le projet hass-prixcarburant (Aohzan) ;
2. fichier stations_name.json éventuellement présent à côté de l'intégration ;
3. sinon, noms génériques « Station <ville> ».
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STATIONS_NAME_FILE, STATIONS_NAME_URL

_LOGGER = logging.getLogger(__name__)
_KEY = f"{DOMAIN}_names"
_LOCK = f"{DOMAIN}_names_lock"
REFRESH = 7 * 24 * 3600


async def async_get_station_names(hass: HomeAssistant) -> dict[str, dict]:
    if _KEY in hass.data:
        return hass.data[_KEY]
    lock = hass.data.setdefault(_LOCK, asyncio.Lock())
    async with lock:
        if _KEY in hass.data:
            return hass.data[_KEY]
        store: Store = Store(hass, 1, f"{DOMAIN}.station_names")
        cached = await store.async_load() or {}
        names: dict = cached.get("names") or {}
        if not names or time.time() - cached.get("ts", 0) > REFRESH:
            try:
                async with asyncio.timeout(30):
                    resp = await async_get_clientsession(hass).get(STATIONS_NAME_URL)
                    resp.raise_for_status()
                    fresh = await resp.json(content_type=None)
                if isinstance(fresh, dict) and len(fresh) > 1000:
                    names = fresh
                    await store.async_save({"ts": time.time(), "names": names})
            except (ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Liste des noms de stations non téléchargée : %s", err)
        if not names:
            path = Path(__file__).parent / STATIONS_NAME_FILE

            def _load() -> dict:
                try:
                    with open(path, encoding="utf-8") as fh:
                        return json.load(fh)
                except (OSError, ValueError):
                    return {}

            names = await hass.async_add_executor_job(_load)
        hass.data[_KEY] = names
        return names
