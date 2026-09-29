"""Noms, enseignes et logos des stations (le flux gouvernemental ne les fournit pas).

Sources ouvertes, sans dépendance à une autre intégration :
- OpenStreetMap (API Overpass) : stations `amenity=fuel`, reliées au flux officiel
  par l'étiquette `ref:FR:prix-carburants` (sinon par proximité, 150 m max) ;
- Wikidata / Wikimedia Commons : logo de l'enseigne (`brand:wikidata` -> P154).

Tout est mis en cache dans le stockage de Home Assistant : les stations d'une zone
sont relues chaque semaine, les logos chaque mois. En cas d'indisponibilité, le cache
est utilisé et, à défaut, la station s'appelle « Station <ville> ».
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import unicodedata
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
COMMONS_FILE_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=128"
USER_AGENT = f"HomeAssistant-carburant_holm/{VERSION} (+https://github.com/kaaribou/carburant-holm)"

ZONE_REFRESH = 7 * 24 * 3600
LOGO_REFRESH = 30 * 24 * 3600
RETRY_AFTER = 6 * 3600  # après un échec, on ne réessaie pas avant 6 h
MATCH_DISTANCE_M = 150

_KEY = f"{DOMAIN}_osm"
_LOCK = f"{DOMAIN}_osm_lock"

GENERIC_NAMES = {"stationservice", "station", "stationessence", "carburant", "carburants", "fuel", "gasstation"}


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    x = (lon2 - lon1) * p * math.cos((lat1 + lat2) * p / 2)
    y = (lat2 - lat1) * p
    return 6371000 * math.hypot(x, y)


def _pretty(text: str) -> str:
    """Met en forme un nom écrit tout en majuscules."""
    if not text.isupper():
        return text
    small = {"de", "du", "des", "la", "le", "les", "en", "sur", "et", "d", "l", "a", "au", "aux"}
    words = text.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


class OsmCache:
    """Cache partagé entre toutes les zones."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, 1, f"{DOMAIN}.osm")
        self._lock = asyncio.Lock()
        self.areas: dict[str, float] = {}  # clé de zone -> horodatage
        self.fails: dict[str, float] = {}
        self.elements: dict[str, dict] = {}  # id OSM -> {ref, lat, lon, name, brand, qid}
        self.logos: dict[str, dict] = {}  # Qxxx -> {url, ts}
        self._by_ref: dict[str, str] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.areas = data.get("areas", {})
        self.elements = data.get("elements", {})
        self.logos = data.get("logos", {})
        self._index()

    def _index(self) -> None:
        self._by_ref = {e["ref"]: oid for oid, e in self.elements.items() if e.get("ref")}

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"areas": self.areas, "elements": self.elements, "logos": self.logos}, 10)

    # ------------------------------------------------------------------ OSM
    async def _overpass(self, query: str) -> list[dict]:
        session = async_get_clientsession(self.hass)
        last: Exception | None = None
        for url in OVERPASS_URLS:
            try:
                async with asyncio.timeout(90):
                    async with session.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}) as resp:
                        if resp.status != 200:
                            raise ClientError(f"HTTP {resp.status}")
                        return (await resp.json(content_type=None)).get("elements") or []
            except (ClientError, TimeoutError, ValueError) as err:
                last = err
        raise ClientError(str(last))

    async def _ensure_areas(self, areas: dict[str, tuple[float, float, int]]) -> None:
        now = time.time()
        todo = {
            k: v for k, v in areas.items()
            if now - self.areas.get(k, 0) > ZONE_REFRESH and now - self.fails.get(k, 0) > RETRY_AFTER
        }
        if not todo:
            return
        parts = "".join(f'nwr["amenity"="fuel"](around:{r},{lat:.6f},{lon:.6f});' for lat, lon, r in todo.values())
        query = f"[out:json][timeout:60];({parts});out center tags;"
        try:
            elements = await self._overpass(query)
        except ClientError as err:
            _LOGGER.warning("OpenStreetMap indisponible, noms des stations en cache utilisés : %s", err)
            for k in todo:
                self.fails[k] = now
            return
        for el in elements:
            tags = el.get("tags") or {}
            lat = el.get("lat", (el.get("center") or {}).get("lat"))
            lon = el.get("lon", (el.get("center") or {}).get("lon"))
            if lat is None or lon is None:
                continue
            self.elements[f"{el.get('type', 'n')[0]}{el.get('id')}"] = {
                "ref": str(tags.get("ref:FR:prix-carburants") or "").strip() or None,
                "lat": lat,
                "lon": lon,
                "name": (tags.get("name") or "").strip(),
                "brand": (tags.get("brand") or tags.get("operator") or "").strip(),
                "qid": (tags.get("brand:wikidata") or "").split(";")[0].strip() or None,
            }
        for k in todo:
            self.areas[k] = now
        self._index()
        self._save()

    # ------------------------------------------------------------------ logos
    async def _ensure_logos(self, qids: set[str]) -> None:
        now = time.time()
        todo = [q for q in qids if re.fullmatch(r"Q\d+", q) and now - self.logos.get(q, {}).get("ts", 0) > LOGO_REFRESH]
        if not todo:
            return
        session = async_get_clientsession(self.hass)
        for i in range(0, len(todo), 50):
            chunk = todo[i : i + 50]
            try:
                async with asyncio.timeout(30):
                    async with session.get(
                        WIKIDATA_URL,
                        params={"action": "wbgetentities", "ids": "|".join(chunk), "props": "claims", "format": "json"},
                        headers={"User-Agent": USER_AGENT},
                    ) as resp:
                        data = await resp.json(content_type=None)
            except (ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Logos Wikidata non récupérés : %s", err)
                return
            for qid, ent in (data.get("entities") or {}).items():
                claims = [c for c in (ent.get("claims") or {}).get("P154", []) if c.get("rank") != "deprecated"]
                claims.sort(key=lambda c: c.get("rank") == "preferred")
                file = ((claims[-1].get("mainsnak") or {}).get("datavalue") or {}).get("value") if claims else None
                url = COMMONS_FILE_URL.format(quote(file.replace(" ", "_"))) if file else None
                self.logos[qid] = {"url": url, "ts": now}
        self._save()

    # ------------------------------------------------------------------ recherche
    def _match(self, sid: str, lat: float | None, lon: float | None) -> dict | None:
        oid = self._by_ref.get(sid)
        if oid:
            return self.elements[oid]
        if lat is None or lon is None:
            return None
        best, best_d = None, MATCH_DISTANCE_M
        for e in self.elements.values():
            if e.get("ref") and e["ref"] != sid:
                continue
            if abs(e["lat"] - lat) > 0.003 or abs(e["lon"] - lon) > 0.005:
                continue
            d = _dist_m(lat, lon, e["lat"], e["lon"])
            if d < best_d:
                best, best_d = e, d
        return best

    def info(self, sid: str, lat: float | None, lon: float | None) -> dict:
        """Nom OSM (brut), enseigne et logo d'une station ; vide si inconnue."""
        e = self._match(sid, lat, lon) or {}
        brand = e.get("brand") or ""
        name = _pretty(e.get("name") or "")
        if _norm(name) in GENERIC_NAMES or _norm(name) == _norm(brand):
            name = ""
        logo = (self.logos.get(e.get("qid") or "") or {}).get("url")
        return {"name": name, "brand": brand, "logo": logo}

    async def async_infos(self, recs: list[dict], zones: list[tuple[float, float, float]]) -> dict[str, dict]:
        """Infos (nom, enseigne, logo) des stations brutes `recs`.

        `zones` : (lat, lon, rayon en km) des zones à couvrir ; une station hors zone
        (favorite éloignée) est couverte par une petite zone autour d'elle.
        """
        async with self._lock:
            areas: dict[str, tuple[float, float, int]] = {}
            for lat, lon, radius in zones:
                areas[f"{lat:.3f},{lon:.3f},{radius:g}"] = (lat, lon, int(radius * 1000) + 300)
            coords = {str(r.get("id")): _coords(r) for r in recs}
            for sid, (lat, lon) in coords.items():
                if lat is not None and not any(_dist_m(lat, lon, zl, zo) <= r * 1000 for zl, zo, r in zones):
                    areas[f"st{sid}"] = (lat, lon, 300)
            await self._ensure_areas(areas)
            matches = {sid: self._match(sid, *c) for sid, c in coords.items()}
            await self._ensure_logos({m["qid"] for m in matches.values() if m and m.get("qid")})
            return {sid: self.info(sid, *c) for sid, c in coords.items()}


def _coords(rec: dict) -> tuple[float | None, float | None]:
    geom = rec.get("geom") or {}
    lat, lon = geom.get("lat"), geom.get("lon")
    if lat is None or lon is None:
        try:
            return float(rec.get("latitude")) / 100000, float(rec.get("longitude")) / 100000
        except (TypeError, ValueError):
            return None, None
    return lat, lon


async def async_get_osm(hass: HomeAssistant) -> OsmCache:
    """Cache OSM partagé (chargé une seule fois)."""
    if _KEY in hass.data:
        return hass.data[_KEY]
    lock = hass.data.setdefault(_LOCK, asyncio.Lock())
    async with lock:
        if _KEY not in hass.data:
            cache = OsmCache(hass)
            await cache.async_load()
            hass.data[_KEY] = cache
    return hass.data[_KEY]
