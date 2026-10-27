"""Plate readers in OpenStreetMap: nodes tagged surveillance:type=ALPR (the data DeFlock maps).

One Overpass query over the city plus its ring. The response, with Overpass's own data timestamp,
is cached in data/raw/osm; rerun with --refresh to pull current OSM. OSM data is ODbL:
anything derived from it carries the "© OpenStreetMap contributors" attribution.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config
from .common import RAW, USER_AGENT, buffered, city_geometry, log, now_iso

ENDPOINT = "https://overpass-api.de/api/interpreter"
CACHE = RAW / "osm" / "alpr_overpass.json"


def query() -> str:
    west, south, east, north = buffered(city_geometry(), config.RING_MILES + 0.5).bounds
    key, value = config.OSM_TAG
    return (f'[out:json][timeout:120];nwr["{key}"="{value}"]'
            f"({south:.3f},{west:.3f},{north:.3f},{east:.3f});out center tags;")


def fetch(*, refresh: bool = False) -> dict[str, Any]:
    if CACHE.exists() and not refresh:
        return json.loads(CACHE.read_text())
    body = urlencode({"data": query()}).encode()
    with urlopen(Request(ENDPOINT, data=body, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
        payload = json.loads(response.read().decode())
    payload["panopticounts_fetched_at"] = now_iso()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload))
    log(f"osm: {len(payload['elements']):,} ALPR elements, OSM data as of {payload['osm3s']['timestamp_osm_base']}")
    return payload


def readers() -> tuple[list[dict[str, Any]], str]:
    """[{osm_id, lat, lon, manufacturer, operator}], and the OSM data timestamp."""
    payload = fetch()
    out = []
    for element in payload["elements"]:
        lat = element.get("lat", element.get("center", {}).get("lat"))
        lon = element.get("lon", element.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = element.get("tags", {})
        out.append({"osm_id": f"{element['type']}/{element['id']}", "lat": lat, "lon": lon,
                    "manufacturer": tags.get("manufacturer", ""), "operator": tags.get("operator", "")})
    return out, payload["osm3s"]["timestamp_osm_base"]
