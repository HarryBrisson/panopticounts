"""Decoder, merge rules, and the publish guard (no coordinates in any released file)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from shapely.geometry import box

from panopticounts import build, proton
from panopticounts.common import LAYERS, PROCESSED

RING = box(-87.70, 41.85, -87.60, 41.95)


def _inventory_bytes(rows):
    """rows: (lat, lon, county, days, type, status, flags) -> Proton's columnar 15-byte layout."""
    cols = list(zip(*rows))
    return b"".join([np.array([round(v * 1e5) for v in cols[0]], np.int32).tobytes(),
                     np.array([round(v * 1e5) for v in cols[1]], np.int32).tobytes(),
                     np.array(cols[2], np.uint16).tobytes(), np.array(cols[3], np.uint16).tobytes(),
                     np.array(cols[4], np.uint8).tobytes(), np.array(cols[5], np.uint8).tobytes(),
                     np.array(cols[6], np.uint8).tobytes()])


def test_decoder_reads_the_columnar_layout(tmp_path, monkeypatch):
    plates = proton.capability_mask(["readsLicensePlates"])
    raw = _inventory_bytes([(41.9, -87.65, 3, 100, 0, 0, plates), (41.8, -87.7, 65535, 65535, 3, 2, 0)])
    script = ("{n:2,zipCount:1,types:`falcon.picard.wing.raven`}"
              "[`inService`,`inPlanning`,`decommissioned`,`unknown`]"
              "[`livestream`,`readsLicensePlates`,`lpr`,`supportsVehicleDescriptionAlerts`,`replay`,`supportsFreeFormSearchPeople`]")
    (tmp_path / "c.bin").write_bytes(raw)
    (tmp_path / "m.js").write_text(script)
    monkeypatch.setattr(proton, "CACHE", tmp_path)
    monkeypatch.setattr(proton, "fetch", lambda **_: {"cameras": {"file": "c.bin"}, "script": {"file": "m.js"}})
    inv = proton.load()
    assert inv["n"] == 2 and inv["types"][inv["type"][1]] == "raven"
    assert abs(inv["lat"][0] - 41.9) < 1e-9 and inv["status"][1] == 2
    assert inv["flags"][0] & plates and proton.added_date(inv["days"][0]) == "2024-04-10"
    assert proton.added_date(inv["days"][1]) is None


def test_merge_rules():
    # Flock devices: in service (plates), decommissioned, planned.
    flock = {"lat": np.array([41.90, 41.91, 41.92]), "lon": np.array([-87.65, -87.65, -87.65]),
             "status": np.array(["inService", "decommissioned", "inPlanning"]),
             "type": np.array(["falcon"] * 3), "days": np.array([100, 100, 100]),
             "plate": np.array([True, False, False])}
    near = 0.0002  # ~20 m
    osm = [
        {"osm_id": "node/1", "lat": 41.90 + near, "lon": -87.65, "manufacturer": "Flock Safety", "operator": ""},
        {"osm_id": "node/2", "lat": 41.91 + near, "lon": -87.65, "manufacturer": "", "operator": ""},
        {"osm_id": "node/3", "lat": 41.92 + near, "lon": -87.65, "manufacturer": "Flock Safety", "operator": ""},
        {"osm_id": "node/4", "lat": 41.90 + near, "lon": -87.65, "manufacturer": "Motorola Solutions", "operator": "CPD"},
        {"osm_id": "node/5", "lat": 41.93, "lon": -87.62, "manufacturer": "Flock Safety", "operator": ""},
    ]
    counted, stats = build.merge(flock, osm, RING)
    sources = sorted((d["source"], d.get("osm_id", "")) for d in counted)
    assert sources == [("osm", "node/4"), ("osm", "node/5"), ("osm+proton", "node/3"), ("proton", "")]
    assert stats["osm_matched_flock_in_service"] == 1 and stats["osm_matched_flock_decommissioned"] == 1
    assert sum(d["flock"] for d in counted) == 3  # proton, planned-then-mapped, Flock not in inventory


def test_released_files_carry_no_coordinates():
    """The release is aggregates only: no lat/lon columns, no geometry, no device ids."""
    forbidden = {"lat", "lon", "latitude", "longitude", "geometry", "osm_id", "coordinates"}
    files = sorted(LAYERS.glob("*.csv"))
    assert files, "build first"
    for path in files:
        header = set(next(csv.reader(path.open())))
        assert not header & forbidden, path
    text = (LAYERS / "aggregation_spec.json").read_text() + (PROCESSED / "summary.json").read_text()
    assert '"coordinates"' not in text and '"lat"' not in text
    assert not list(LAYERS.glob("*.geojson")) and not list(LAYERS.glob("*.bin"))


def test_every_metric_is_a_contested_direction_left_unweighted():
    """Privacy vs public safety is the reader's call (byop v1.4): each metric says so, keeps a default
    direction for consumers that predate the flag, and enters nobody's index unasked."""
    metrics = json.loads((LAYERS / "aggregation_spec.json").read_text())["metrics"]
    assert metrics
    for metric_id, metric in metrics.items():
        assert metric["direction_contested"] is True, metric_id
        assert metric["direction"] in ("higher", "lower"), metric_id
        assert metric["default_weight"] == 0, metric_id
        assert "others count the same cameras as a public-safety investment" in metric["methodology"], metric_id
