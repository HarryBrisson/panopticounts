"""Where people live (2020 Census blocks) and the tracts the published layer is keyed by.

Blocks: each populated block's internal point with its 2020 count (POP100), so a tract's or ward's
distance average is weighted by where its residents actually are. Tracts: 2020-vintage Cook County
tract polygons, the same TIGERweb query Penlight uses to place a tract-keyed layer, so the published
GEOIDs match the geometry the consumer supplies. Both from TIGERweb, no key needed.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config
from .common import RAW, USER_AGENT, log, now_iso

BLOCKS_ENDPOINT = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/12/query"
TRACTS_ENDPOINT = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2023/MapServer/8/query"
PAGE = 10_000


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
        payload = json.loads(response.read().decode())
    if "error" in payload:
        raise RuntimeError(f"TIGERweb: {payload['error']}")
    return payload


def blocks(state: str, county: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    path = RAW / "census_blocks" / f"blocks2020_{state}{county}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())["blocks"]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _get_json(f"{BLOCKS_ENDPOINT}?" + urlencode({
            "where": f"STATE='{state}' AND COUNTY='{county}' AND POP100>0",
            "outFields": "GEOID,POP100,INTPTLAT,INTPTLON", "returnGeometry": "false",
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE, "f": "json"}))
        page = [f["attributes"] for f in payload.get("features", [])]
        rows += [{"geoid": r["GEOID"], "pop": int(r["POP100"]),
                  "lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLON"])} for r in page]
        if len(page) < PAGE and not payload.get("exceededTransferLimit"):
            break
        offset += len(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": BLOCKS_ENDPOINT, "fetched_at": now_iso(), "blocks": rows}))
    log(f"blocks: {state}{county} {len(rows):,} populated blocks")
    return rows


def all_blocks(*, refresh: bool = False) -> list[dict[str, Any]]:
    return [b for state, county in config.BLOCK_COUNTIES for b in blocks(state, county, refresh=refresh)]


def tracts(state: str = "17", county: str = "031", *, refresh: bool = False) -> dict[str, Any]:
    """GeoJSON FeatureCollection of the county's tracts (properties.GEOID)."""
    path = RAW / "tracts" / f"tracts2020_{state}{county}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())["geojson"]
    geojson = _get_json(f"{TRACTS_ENDPOINT}?" + urlencode({
        "where": f"STATE='{state}' AND COUNTY='{county}'", "outFields": "GEOID,STATE,COUNTY,TRACT",
        "returnGeometry": "true", "f": "geojson", "outSR": 4326}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": TRACTS_ENDPOINT, "fetched_at": now_iso(), "geojson": geojson}))
    log(f"tracts: {state}{county} {len(geojson['features']):,} tracts")
    return geojson
