"""Flock Safety's device inventory, as Proton's Flock camera map serves it.

https://proton.me/flock-camera-map loads one static file of every device it knows about (a December
2025 snapshot; Proton credits researcher Joshua Michael, who obtained it through an access token
Flock's servers exposed). The file carries no license, so it stays in data/raw and nothing
downstream publishes a device or a coordinate from it, only counts and distances rolled up to
census tracts, wards and community areas.

The file is columnar, 15 bytes per device, in this order:

    lat     int32   degrees x 1e5
    lon     int32   degrees x 1e5
    county  uint16  index into the map script's county list (unused here)
    days    uint16  days after 2024-01-01 the device entered the inventory, 65535 = unknown
    type    uint8   index into the map script's device-type list (falcon, wing, raven, ...)
    status  uint8   index into its status list (inService, inPlanning, decommissioned, unknown)
    flags   uint8   bit i = capability i of its capability list (readsLicensePlates, lpr, ...)

Both file names carry a build hash that changes whenever Proton redeploys, so `fetch` reads them
from the page. The type, status and capability lists are read from the map script too, and checked
against the lists below, so a reordering upstream fails loudly instead of mislabelling devices.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np

from .common import RAW, USER_AGENT, log, now_iso

PAGE_URL = "https://proton.me/flock-camera-map"
BASE_URL = "https://proton.me"
CACHE = RAW / "proton"
SNAPSHOT = CACHE / "snapshot.json"

RECORD_BYTES = 15
EPOCH = date(2024, 1, 1)
UNKNOWN_DAYS = 65535

STATUSES = ["inService", "inPlanning", "decommissioned", "unknown"]
CAPABILITIES = ["livestream", "readsLicensePlates", "lpr", "supportsVehicleDescriptionAlerts", "replay",
                "supportsFreeFormSearchPeople"]


def _get(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=120) as response:
        return response.read()


def fetch(*, refresh: bool = False) -> dict[str, Any]:
    """Download the page, its camera file and its map script into data/raw/proton (once)."""
    if SNAPSHOT.exists() and not refresh:
        return json.loads(SNAPSHOT.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    page = _get(PAGE_URL).decode()
    cameras = re.search(r"camerasUrl&quot;:&quot;([^&]+)&quot;", page)
    script = re.search(r"/_astro/FlockCameraMap\.astro_astro_type_script_index_0_lang\.[\w-]+\.js", page)
    if not cameras or not script:
        raise RuntimeError("Proton's page no longer names its camera file or map script; inspect it by hand")
    snapshot = {"page": PAGE_URL, "fetched_at": now_iso()}
    for key, path in (("cameras", cameras.group(1)), ("script", script.group(0))):
        body = _get(BASE_URL + path)
        (CACHE / Path(path).name).write_bytes(body)
        snapshot[key] = {"url": BASE_URL + path, "file": Path(path).name, "bytes": len(body),
                         "sha256": hashlib.sha256(body).hexdigest()}
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1))
    log(f"proton: {snapshot['cameras']['bytes']:,} bytes -> {CACHE}")
    return snapshot


def _script_lists(script: str) -> dict[str, Any]:
    """The device count and the type list are inlined in the map script as `{n:...,types:`a.b.c`}`."""
    count = re.search(r"\{n:(\d+),zipCount:", script)
    types = re.search(r"types:`([^`]+)`", script)
    if not count or not types:
        raise RuntimeError("could not find the device count / type list in Proton's map script")
    for expected in (STATUSES, CAPABILITIES):
        literal = ",".join(f"`{name}`" for name in expected)
        if f"[{literal}]" not in script:
            raise RuntimeError(f"Proton's map script no longer lists {expected} in that order")
    return {"n": int(count.group(1)), "types": types.group(1).split(".")}


def load() -> dict[str, Any]:
    """Every device in the inventory as numpy columns, plus the label lists."""
    snapshot = fetch()
    raw = (CACHE / snapshot["cameras"]["file"]).read_bytes()
    lists = _script_lists((CACHE / snapshot["script"]["file"]).read_text())
    n = lists["n"]
    if len(raw) != n * RECORD_BYTES:
        raise RuntimeError(f"camera file is {len(raw):,} bytes, expected {n:,} x {RECORD_BYTES}")
    columns = {
        "lat": np.frombuffer(raw, np.int32, n, 0) / 1e5,
        "lon": np.frombuffer(raw, np.int32, n, 4 * n) / 1e5,
        "days": np.frombuffer(raw, np.uint16, n, 10 * n),
        "type": np.frombuffer(raw, np.uint8, n, 12 * n),
        "status": np.frombuffer(raw, np.uint8, n, 13 * n),
        "flags": np.frombuffer(raw, np.uint8, n, 14 * n),
    }
    return {"n": n, "types": lists["types"], "snapshot": snapshot, **columns}


def capability_mask(names: list[str]) -> int:
    return sum(1 << CAPABILITIES.index(name) for name in names)


def added_date(days: int) -> str | None:
    return None if days == UNKNOWN_DAYS else (EPOCH + timedelta(days=int(days))).isoformat()
