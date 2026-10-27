"""Plate readers -> tract, ward and community-area aggregates + the byop/v1 spec.

1. Flock plate readers from Proton's inventory, and every OSM ALPR, inside the city + ring.
2. Merge: an OSM reader with no manufacturer or a Flock tag within MATCH_METERS of a Flock plate
   reader is that device. In service in Dec 2025: counted once, from Proton. Decommissioned: not
   counted (the OSM node is stale). Planned: counted, as installed since. Every other OSM reader
   counts on its own.
3. Each populated 2020 block gets its straight-line distance to the nearest counted reader.
4. Roll up to wards, community areas and tracts: the three published layers. Penlight reads a
   ward or community area straight from its own table and anything else (χGRID cells) from the
   tracts that overlap it. No device, coordinate or block-level row leaves data/raw.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from typing import Any

import numpy as np
import shapely
from shapely import STRtree, points
from shapely.geometry import shape

from . import census, config, osm, proton
from .common import (COMMUNITY_AREAS, LAYERS, METERS_PER_MILE, PROCESSED, RAW, WARDS, buffered, city_geometry,
                     log, now_iso, project, read_geojson, round_float, to_meters)

NEAR_METERS = config.NEAR_MILES * METERS_PER_MILE
TRACT_MIN_CITY_SHARE = 0.01  # a tract is published if at least 1% of it lies inside the city


def flock_plate_readers(ring) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Flock devices in the ring that read plates (any status), and a type/status tally."""
    inv = proton.load()
    inside = shapely.contains_xy(ring, inv["lon"], inv["lat"])
    plate = (inv["flags"] & proton.capability_mask(config.FLOCK_PLATE_CAPABILITIES)) != 0
    # Only in-service units carry capability flags, so planned and decommissioned plate readers are
    # recognised by model; they are kept for matching OSM readers, never counted from Proton.
    plate_model = np.isin(np.array(inv["types"])[inv["type"]], list(config.FLOCK_PLATE_MODELS))
    keep = inside & (plate | plate_model)
    status = np.array(proton.STATUSES)[inv["status"]]
    tally = Counter(f"{inv['types'][t]}/{s}/{'plates' if p else 'no_plates'}"
                    for t, s, p in zip(inv["type"][inside], status[inside], plate[inside]))
    readers = {"lat": inv["lat"][keep], "lon": inv["lon"][keep], "status": status[keep],
               "type": np.array(inv["types"])[inv["type"][keep]], "days": inv["days"][keep], "plate": plate[keep]}
    meta = {"snapshot": inv["snapshot"], "devices_in_inventory": inv["n"], "devices_in_ring": int(inside.sum()),
            "devices_in_ring_by_type_status_capability": dict(tally.most_common())}
    return readers, meta


def merge(flock: dict[str, np.ndarray], osm_readers: list[dict[str, Any]], ring) -> tuple[list[dict], dict]:
    transformer = to_meters()
    fx, fy = transformer.transform(flock["lon"], flock["lat"])
    tree = STRtree(points(fx, fy))
    counted: list[dict[str, Any]] = [
        {"lat": float(lat), "lon": float(lon), "source": "proton", "vendor": "Flock Safety", "flock": True,
         "type": str(kind), "added": proton.added_date(days)}
        for lat, lon, status, kind, days, plate in zip(flock["lat"], flock["lon"], flock["status"], flock["type"],
                                                       flock["days"], flock["plate"])
        if status in config.FLOCK_STATUSES and plate]
    stats: Counter = Counter()
    ring_osm = [r for r in osm_readers if shapely.contains_xy(ring, r["lon"], r["lat"])]
    for reader in ring_osm:
        vendor = reader["manufacturer"] or "unknown"
        mergeable = reader["manufacturer"] in ("", *config.FLOCK_MANUFACTURERS)
        if mergeable and len(flock["lat"]):
            x, y = transformer.transform(reader["lon"], reader["lat"])
            hit, distance = tree.query_nearest(points(x, y), return_distance=True, max_distance=config.MATCH_METERS)
            if len(hit):
                i = int(hit[0])
                status = flock["status"][i]
                if status in config.FLOCK_STATUSES and flock["plate"][i]:
                    stats["osm_matched_flock_in_service"] += 1      # already counted from Proton
                    continue
                if status == "decommissioned":
                    stats["osm_matched_flock_decommissioned"] += 1  # removed by Dec 2025; the OSM node is stale
                    continue
                # Planned (or unknown) in Dec 2025 and mapped in OSM since: installed after the snapshot.
                stats[f"osm_matched_flock_{status}_counted"] += 1
                counted.append({"lat": reader["lat"], "lon": reader["lon"], "source": "osm+proton",
                                "vendor": "Flock Safety", "flock": True, "operator": reader["operator"],
                                "osm_id": reader["osm_id"]})
                continue
        is_flock = reader["manufacturer"] in config.FLOCK_MANUFACTURERS
        stats["osm_counted_flock_not_in_inventory" if is_flock else "osm_counted_other_vendor"] += 1
        counted.append({"lat": reader["lat"], "lon": reader["lon"], "source": "osm", "vendor": vendor,
                        "flock": is_flock, "operator": reader["operator"], "osm_id": reader["osm_id"]})
    stats["osm_in_ring"] = len(ring_osm)
    stats["flock_in_service_in_ring"] = int(sum(s in config.FLOCK_STATUSES and p for s, p in zip(flock["status"], flock["plate"])))
    return counted, dict(stats)


MADISON_STREET_LAT = 41.8819  # Chicago's north/south address baseline


def osm_coverage_of_flock(flock: dict[str, np.ndarray], osm_readers: list[dict[str, Any]], city) -> dict[str, Any]:
    """How many of Flock's in-service plate readers in the city volunteers had mapped in OSM: the one
    check we have on how complete OSM is, north and south of Madison Street."""
    live = np.array([s in config.FLOCK_STATUSES and p for s, p in zip(flock["status"], flock["plate"])], bool)
    live &= shapely.contains_xy(city, flock["lon"], flock["lat"])
    transformer = to_meters()
    mergeable = [r for r in osm_readers if r["manufacturer"] in ("", *config.FLOCK_MANUFACTURERS)]
    ox, oy = transformer.transform([r["lon"] for r in mergeable], [r["lat"] for r in mergeable])
    fx, fy = transformer.transform(flock["lon"][live], flock["lat"][live])
    hit, _ = STRtree(points(ox, oy)).query_nearest(points(fx, fy), return_distance=True,
                                                    max_distance=config.MATCH_METERS, all_matches=False)
    mapped = np.zeros(int(live.sum()), bool)
    mapped[hit[0]] = True
    north = flock["lat"][live] >= MADISON_STREET_LAT
    out = {}
    for name, sel in (("city", np.ones_like(north)), ("north_of_madison", north), ("south_of_madison", ~north)):
        n = int(sel.sum())
        out[name] = {"flock_in_service": n, "mapped_in_osm": int(mapped[sel].sum()),
                     "share_mapped_pct": round_float(100 * mapped[sel].sum() / n, 1) if n else None}
    return out


def nearest_meters(devices: list[dict], lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    transformer = to_meters()
    dx, dy = transformer.transform([d["lon"] for d in devices], [d["lat"] for d in devices])
    bx, by = transformer.transform(lons, lats)
    _, distance = STRtree(points(dx, dy)).query_nearest(points(bx, by), return_distance=True, all_matches=False)
    return np.asarray(distance)


def _counts_in(polygons: list, devices: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Readers (all, Flock) inside each polygon (WGS84)."""
    tree = STRtree(polygons)
    pts = points([d["lon"] for d in devices], [d["lat"] for d in devices])
    device_idx, poly_idx = tree.query(pts, predicate="intersects")
    total = np.zeros(len(polygons), int)
    flock = np.zeros(len(polygons), int)
    for di, pi in zip(device_idx, poly_idx):
        total[pi] += 1
        flock[pi] += devices[di]["flock"]
    return total, flock


def _row(area_km2: float, pop: int, pop_near: int, pop_x_m: float, readers: int, flock: int) -> dict[str, Any]:
    has_pop = pop > 0
    return {
        "area_km2": round_float(area_km2, 4),
        "population": pop,
        "plate_readers": readers,
        "flock_plate_readers": flock,
        "plate_readers_per_sqkm": round_float(readers / area_km2, 4),
        "flock_plate_readers_per_sqkm": round_float(flock / area_km2, 4),
        "plate_readers_per_10k": round_float(readers / pop * config.PER_POPULATION, 3) if has_pop else None,
        "residents_near_reader_pct": round_float(100 * pop_near / pop, 2) if has_pop else None,
        "nearest_reader_mi": round_float(pop_x_m / pop / METERS_PER_MILE, 3) if has_pop else None,
    }


def _rollup(groups: np.ndarray, n: int, pop: np.ndarray, near: np.ndarray, meters: np.ndarray):
    """Per-group population, population near a reader, and Σ pop x distance."""
    pop_sum = np.bincount(groups, weights=pop, minlength=n)
    near_sum = np.bincount(groups, weights=pop * near, minlength=n)
    dist_sum = np.bincount(groups, weights=pop * meters, minlength=n)
    return pop_sum, near_sum, dist_sum


def build() -> dict[str, Any]:
    city = city_geometry()
    ring = buffered(city, config.RING_MILES)
    flock, flock_meta = flock_plate_readers(ring)
    osm_readers, osm_as_of = osm.readers()
    devices, merge_stats = merge(flock, osm_readers, ring)
    log(f"readers counted in city+ring: {len(devices):,} ({merge_stats})")

    blocks = census.all_blocks()
    b_lat = np.array([b["lat"] for b in blocks])
    b_lon = np.array([b["lon"] for b in blocks])
    b_pop = np.array([b["pop"] for b in blocks], float)
    b_tract = np.array([b["geoid"][:11] for b in blocks])

    # Tracts at least 1% inside the city; their blocks may sit just outside it, so check that each
    # one's nearest reader is closer than the ring's edge (else an unseen reader could be nearer).
    city_m = project(city)
    tracts = []
    for feature in census.tracts()["features"]:
        geom = shape(feature["geometry"]).buffer(0)
        geom_m = project(geom)
        if geom_m.intersection(city_m).area >= TRACT_MIN_CITY_SHARE * geom_m.area:
            tracts.append((feature["properties"]["GEOID"], geom, geom_m.area / 1e6))
    tract_index = {geoid: i for i, (geoid, _g, _a) in enumerate(tracts)}
    in_tracts = np.array([t in tract_index for t in b_tract])
    in_city = shapely.contains_xy(city, b_lon, b_lat)
    use = in_tracts | in_city
    lat, lon, pop = b_lat[use], b_lon[use], b_pop[use]
    meters = nearest_meters(devices, lat, lon)
    bx, by = to_meters().transform(lon, lat)
    edge = shapely.distance(project(ring).boundary, points(bx, by))
    inexact = int((meters > edge).sum())
    if inexact:
        raise RuntimeError(f"{inexact} blocks' nearest reader is farther than the ring edge; widen RING_MILES")
    near = (meters <= NEAR_METERS).astype(float)

    # Tracts (the published layer).
    groups = np.array([tract_index.get(t, -1) for t in b_tract[use]])
    mask = groups >= 0
    pop_sum, near_sum, dist_sum = _rollup(groups[mask], len(tracts), pop[mask], near[mask], meters[mask])
    t_total, t_flock = _counts_in([g for _i, g, _a in tracts], devices)
    tract_rows = [{"geoid": geoid, **_row(area, int(pop_sum[i]), int(near_sum[i]), dist_sum[i], int(t_total[i]),
                                          int(t_flock[i]))}
                  for i, (geoid, _g, area) in enumerate(tracts)]

    # Wards and community areas, natively: blocks by internal point, readers by location.
    city_lat, city_lon, city_pop = lat[in_city[use]], lon[in_city[use]], pop[in_city[use]]
    city_meters, city_near = meters[in_city[use]], near[in_city[use]]
    area_tables = {}
    for name, path, id_key in (("wards", WARDS, "ward_id"), ("community_areas", COMMUNITY_AREAS, "community_area_id")):
        features = read_geojson(path)["features"]
        polys = [shape(f["geometry"]).buffer(0) for f in features]
        block_poly = np.full(len(city_lat), -1)
        hit_block, hit_poly = STRtree(polys).query(points(city_lon, city_lat), predicate="intersects")
        block_poly[hit_block] = hit_poly
        ok = block_poly >= 0
        p_sum, n_sum, d_sum = _rollup(block_poly[ok], len(polys), city_pop[ok], city_near[ok], city_meters[ok])
        a_total, a_flock = _counts_in(polys, devices)
        area_tables[name] = [
            {id_key: f["properties"][id_key], "name": f["properties"].get("display_name") or f["properties"].get("name"),
             **_row(project(polys[i]).area / 1e6, int(p_sum[i]), int(n_sum[i]), d_sum[i], int(a_total[i]),
                    int(a_flock[i]))}
            for i, f in enumerate(features)]

    city_total, city_flock = _counts_in([city], devices)
    citywide = _row(city_m.area / 1e6, int(city_pop.sum()), int((city_pop * city_near).sum()),
                    float((city_pop * city_meters).sum()), int(city_total[0]), int(city_flock[0]))
    vendors = Counter(d["vendor"] for d in devices if shapely.contains_xy(city, d["lon"], d["lat"]))
    operators = Counter(d.get("operator") or "(not tagged)" for d in devices
                        if d["source"] == "osm" and shapely.contains_xy(city, d["lon"], d["lat"]))

    as_of = osm_as_of[:10]
    _write_csv(LAYERS / "tracts.csv", tract_rows)
    for name, rows in area_tables.items():
        _write_csv(LAYERS / f"{name}.csv", rows)
    coverage = osm_coverage_of_flock(flock, osm_readers, city)
    (LAYERS / "aggregation_spec.json").write_text(json.dumps(spec(as_of, flock_meta, coverage), indent=1) + "\n")
    summary = {
        "built_at": now_iso(), "osm_data_as_of": osm_as_of, "flock_inventory": flock_meta["snapshot"],
        "parameters": {"ring_miles": config.RING_MILES, "near_miles": config.NEAR_MILES,
                       "match_meters": config.MATCH_METERS, "per_population": config.PER_POPULATION},
        "citywide": citywide, "readers_in_city_by_vendor": dict(vendors.most_common()),
        "osm_readers_in_city_by_operator": dict(operators.most_common(15)),
        "merge": merge_stats, "osm_coverage_of_flock": coverage,
        "flock_devices_in_ring": flock_meta["devices_in_ring_by_type_status_capability"],
        "tracts_published": len(tract_rows), "blocks_used": int(use.sum()),
    }
    (PROCESSED / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    # The device list (coordinates) is for checking the merge by hand; it never leaves data/raw.
    (RAW / "devices_counted.json").write_text(json.dumps(devices))
    log(f"citywide: {citywide}")
    return summary


def _write_csv(path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: ("" if v is None else v) for k, v in row.items()} for row in rows)


SOURCES_SENTENCE = (
    "License-plate readers of every vendor from OpenStreetMap (surveillance:type=ALPR; © OpenStreetMap "
    "contributors), merged with Flock Safety's in-service plate readers from the December 2025 device inventory "
    "behind Proton's Flock camera map; an OSM reader within 50 m of a Flock device is treated as that device.")
DIRECTION_SENTENCE = ("Fewer readers is scored as better here, a privacy reading; others count the same cameras "
                      "as a public-safety investment, so the measure is unweighted by default.")


def coverage_caveat(coverage: dict[str, Any]) -> str:
    c, n, s = (coverage[k]["share_mapped_pct"] for k in ("city", "north_of_madison", "south_of_madison"))
    return (f"Non-Flock readers come only from volunteer mapping in OpenStreetMap, which is incomplete: OSM had "
            f"mapped {c:g}% of Flock's in-service readers in the city ({n:g}% north of Madison Street, {s:g}% "
            f"south), so non-Flock readers are likely undercounted, more so on the South Side. Flock's inventory "
            f"is complete but dates from December 2025.")

LAYER_ROW = ("its area, 2020 population, the plate readers inside it, and how far its residents live from the "
             "nearest one")

ICON = ('<path d="M3 6.5 L12.5 4 L14 9 L4.5 11.5 Z"/><path d="M14.2 5.6 L17 4.9"/><path d="M14.7 7.6 L17.5 6.9"/>'
        '<path d="M8.5 10.6 L9.3 14 L5 14 L5 17.5"/>')
KEYWORDS = "license plate reader readers lpr alpr flock camera cameras surveillance police tracking privacy"


def spec(as_of: str, flock_meta: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    common = {"layer": ["wards", "community_areas", "tracts"], "combine": "weighted_mean", "category": "good_governance", "default_weight": 0,
              "time_invariant": False, "keywords": KEYWORDS, "extra_categories": ["safety"], "icon_svg": ICON}
    tract_note = ("Computed directly for wards and community areas; other boundaries take the 2020 census "
                  "tracts that overlap them.")
    return {
        "contract": "byop/v1",
        "source": config.SOURCE,
        "source_url": config.SOURCE_URL,
        "caveat": coverage_caveat(coverage),
        "layers": {
            "wards": {
                "file": "wards.csv", "kind": "polygon_values", "id_field": "ward_id",
                "geography": "chicago_ward_2023", "as_of": as_of,
                "value": f"a Chicago ward (2023 map): {LAYER_ROW}",
            },
            "community_areas": {
                "file": "community_areas.csv", "kind": "polygon_values", "id_field": "community_area_id",
                "geography": "chicago_community_area", "as_of": as_of,
                "value": f"a Chicago community area: {LAYER_ROW}",
            },
            "tracts": {
                "file": "tracts.csv", "kind": "polygon_values", "id_field": "geoid",
                "geography": "census_tract_2020", "as_of": as_of,
                "value": f"a 2020 Cook County census tract at least 1% inside Chicago: {LAYER_ROW}",
            },
        },
        "metrics": {
            "alpr_per_sqkm": {
                **common, "value": "plate_readers_per_sqkm", "weight": "area_km2", "unit": "rate",
                "direction": "lower", "label": "Plate Reader Density",
                "description": "License-plate-reading cameras per square kilometer.",
                "methodology": f"{SOURCES_SENTENCE} Readers counted per area and divided by its land area. {tract_note} "
                               f"{DIRECTION_SENTENCE}",
            },
            "alpr_per_10k_residents": {
                **common, "value": "plate_readers_per_10k", "weight": "population", "unit": "rate",
                "direction": "lower", "label": "Plate Readers per 10k",
                "description": "License-plate-reading cameras per 10,000 residents.",
                "methodology": f"{SOURCES_SENTENCE} Readers per 10,000 residents (2020 Census). "
                               f"{tract_note} {DIRECTION_SENTENCE}",
            },
            "residents_near_alpr_pct": {
                **common, "value": "residents_near_reader_pct", "weight": "population", "unit": "percent",
                "direction": "lower", "label": "Near a Plate Reader",
                "description": "Share of residents who live within a quarter mile of a license-plate-reading camera.",
                "methodology": f"{SOURCES_SENTENCE} Straight-line distance from each populated 2020 Census block to "
                               f"the closest reader, including readers up to {config.RING_MILES:g} miles outside "
                               f"the city. {tract_note} {DIRECTION_SENTENCE}",
            },
            "alpr_nearest_mi": {
                **common, "value": "nearest_reader_mi", "weight": "population", "unit": "miles",
                "direction": "higher", "label": "Nearest Plate Reader",
                "description": "How far the average resident lives from the closest license-plate-reading camera.",
                "methodology": f"{SOURCES_SENTENCE} Straight-line distance from each populated 2020 Census block to "
                               f"the closest reader (including readers up to {config.RING_MILES:g} miles outside "
                               f"the city), averaged over residents. {tract_note} {DIRECTION_SENTENCE}",
            },
            "flock_alpr_per_sqkm": {
                **common, "value": "flock_plate_readers_per_sqkm", "weight": "area_km2", "unit": "rate",
                "direction": "lower", "label": "Flock Reader Density",
                "description": "Flock Safety license-plate-reading cameras per square kilometer.",
                "methodology": "Flock Safety's in-service plate readers from the December 2025 device inventory "
                               "behind Proton's Flock camera map, plus Flock readers mapped in OpenStreetMap since "
                               "(© OpenStreetMap contributors). Counted per area and divided by its land area. "
                               f"{tract_note} {DIRECTION_SENTENCE}",
            },
        },
        "parameters": {"ring_miles": config.RING_MILES, "near_miles": config.NEAR_MILES,
                       "match_meters": config.MATCH_METERS, "per_population": config.PER_POPULATION},
        "provenance": {
            "flock_inventory": {"page": flock_meta["snapshot"]["page"],
                                "sha256": flock_meta["snapshot"]["cameras"]["sha256"],
                                "note": "Not redistributed; only tract aggregates are published."},
            "osm": {"query": osm.query(), "license": "ODbL 1.0, © OpenStreetMap contributors"},
        },
    }
