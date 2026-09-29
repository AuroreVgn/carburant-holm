"""Client de l'API « Prix des carburants en France – flux instantané v2 »."""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import API_URL, FUELS, GEO_API_URL, MAX_ZONE_STATIONS

_LOGGER = logging.getLogger(__name__)
PAGE = 100


class FuelApiError(Exception):
    """Erreur de communication avec l'API."""


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en km entre deux points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _title(text: str | None) -> str:
    if not text:
        return ""
    small = {"de", "du", "des", "la", "le", "les", "en", "sur", "et", "d", "l", "a", "au", "aux"}
    def cap(w: str, first: bool) -> str:
        parts = w.split("-")
        return "-".join(p if ((not first or j) and p in small) else p[:1].upper() + p[1:] for j, p in enumerate(parts))

    words = str(text).lower().split()
    return " ".join(w if (i and w in small) else cap(w, i == 0) for i, w in enumerate(words))


def parse_station(rec: dict[str, Any], infos: dict[str, dict], center: tuple[float, float] | None) -> dict[str, Any] | None:
    """Transforme un enregistrement brut de l'API en station exploitable.

    `infos` : nom / enseigne / logo par identifiant de station (voir osm.py).
    """
    try:
        sid = str(rec["id"])
    except KeyError:
        return None
    geom = rec.get("geom") or {}
    lat = geom.get("lat")
    lon = geom.get("lon")
    if lat is None or lon is None:
        try:
            lat = float(rec.get("latitude")) / 100000
            lon = float(rec.get("longitude")) / 100000
        except (TypeError, ValueError):
            lat = lon = None
    fuels: dict[str, dict] = {}
    for key in FUELS:
        price = rec.get(f"{key}_prix")
        rupture = rec.get(f"{key}_rupture_type")
        if price is not None:
            try:
                fuels[key] = {"price": round(float(price), 3), "updated": rec.get(f"{key}_maj")}
            except (TypeError, ValueError):
                continue
        elif rupture:
            fuels[key] = {"price": None, "shortage": rupture, "shortage_since": rec.get(f"{key}_rupture_debut")}
    info = infos.get(sid) or {}
    brand = (info.get("brand") or "").strip()
    city = _title(rec.get("ville"))
    name = (info.get("name") or "").strip() or f"{brand or 'Station'} {city}".strip()
    services = rec.get("services_service") or []
    if isinstance(services, str):
        services = [s.strip() for s in services.split("//") if s.strip()]
    station = {
        "id": sid,
        "name": name,
        "brand": brand,
        "logo": info.get("logo"),
        "address": _title(rec.get("adresse")),
        "postal_code": rec.get("cp"),
        "city": city,
        "latitude": lat,
        "longitude": lon,
        "distance": None,
        "fuels": fuels,
        "services": services,
        "automate_24_24": str(rec.get("horaires_automate_24_24") or "").lower() == "oui",
    }
    if center and lat is not None and lon is not None:
        station["distance"] = round(haversine(center[0], center[1], lat, lon), 2)
    return station


class FuelApi:
    """Accès aux données gouvernementales."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def _get(self, url: str, params: dict[str, Any], retries: int = 3) -> Any:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                async with asyncio.timeout(30):
                    async with self._session.get(url, params=params) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            raise FuelApiError(f"HTTP {resp.status}: {body[:200]}")
                        return await resp.json(content_type=None)
            except (ClientError, TimeoutError, FuelApiError) as err:
                last = err
                if attempt < retries - 1:
                    await asyncio.sleep(3 * (attempt + 1))
        raise FuelApiError(str(last))

    async def stations_in_zone(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        where = f"distance(geom, geom'POINT({lon} {lat})', {float(radius_km):g}km)"
        out: list[dict] = []
        offset = 0
        total = None
        while offset < MAX_ZONE_STATIONS:
            data = await self._get(API_URL, {"where": where, "limit": PAGE, "offset": offset, "timezone": "Europe/Paris", "lang": "fr"})
            results = data.get("results") or []
            out.extend(results)
            total = data.get("total_count", len(out)) if total is None else total
            offset += PAGE
            if len(results) < PAGE or offset >= total:
                break
        return out

    async def stations_by_ids(self, ids: list[str]) -> list[dict]:
        out: list[dict] = []
        clean = [str(int(i)) for i in ids if str(i).isdigit()]
        for i in range(0, len(clean), 50):
            chunk = clean[i : i + 50]
            data = await self._get(API_URL, {"where": f"id IN ({','.join(chunk)})", "limit": len(chunk), "timezone": "Europe/Paris", "lang": "fr"})
            out.extend(data.get("results") or [])
        return out

    async def search_communes(self, name: str) -> list[dict]:
        params = {"nom": name, "fields": "nom,code,codesPostaux,centre,departement", "boost": "population", "limit": 8}
        if name.strip().isdigit() and len(name.strip()) == 5:
            params = {"codePostal": name.strip(), "fields": "nom,code,codesPostaux,centre,departement", "limit": 8}
        data = await self._get(GEO_API_URL, params)
        res = []
        for c in data or []:
            centre = (c.get("centre") or {}).get("coordinates")
            if not centre:
                continue
            cps = c.get("codesPostaux") or []
            dep = (c.get("departement") or {}).get("code", "")
            res.append({
                "code": c.get("code"),
                "label": f"{c.get('nom')} ({cps[0] if cps else dep})",
                "name": c.get("nom"),
                "longitude": centre[0],
                "latitude": centre[1],
            })
        return res
