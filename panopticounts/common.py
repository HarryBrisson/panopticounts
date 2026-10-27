"""Paths, small IO helpers, geometry."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"              # gitignored: Proton's file, the OSM pull, Census pulls, the matched devices
REFERENCE = DATA / "reference"  # committed inputs: city, ward and community-area boundaries
PROCESSED = DATA / "processed"  # committed: summary.json (sources, merge counts, citywide values)
LAYERS = DATA / "layers"        # the byop/v1 release: aggregation_spec.json + wards/community_areas/tracts.csv

CITY_BOUNDARY = REFERENCE / "chicago_boundary.geojson"
WARDS = REFERENCE / "wards_2023.geojson"
COMMUNITY_AREAS = REFERENCE / "community_areas.geojson"

USER_AGENT = "panopticounts/0.1 (civic research; github.com/HarryBrisson/panopticounts)"
METERS_PER_MILE = 1609.344


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_geojson(path: Path) -> dict:
    return json.loads(path.read_text())


def city_geometry():
    from shapely.geometry import shape

    return shape(read_geojson(CITY_BOUNDARY)["features"][0]["geometry"])


def to_meters():
    """WGS84 -> UTM 16N (metres), always_xy, for buffers, distances and areas."""
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", "EPSG:26916", always_xy=True)


def project(geometry):
    from shapely.ops import transform

    return transform(to_meters().transform, geometry)


def buffered(geometry, miles: float):
    """Buffer a WGS84 geometry by `miles`, return it in WGS84."""
    from pyproj import Transformer
    from shapely.ops import transform

    to_deg = Transformer.from_crs("EPSG:26916", "EPSG:4326", always_xy=True).transform
    return transform(to_deg, project(geometry).buffer(miles * METERS_PER_MILE))


def round_float(value: float, places: int = 4):
    return float(round(value, places)) if value is not None and math.isfinite(value) else None
