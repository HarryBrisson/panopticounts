"""python -m panopticounts <step>

  fetch    Proton's Flock inventory, the OSM ALPR pull, 2020 Census blocks and tracts (cached in data/raw)
  build    merge readers, distances, tract layer + aggregation_spec.json, ward/community-area tables
  all      fetch, then build

Add --refresh to `fetch` to re-download everything (a new Proton build, current OSM).
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> None:
    step = argv[0] if argv else "help"
    refresh = "--refresh" in argv
    if step in {"fetch", "all"}:
        from . import census, osm, proton

        proton.fetch(refresh=refresh)
        osm.fetch(refresh=refresh)
        census.all_blocks(refresh=refresh)
        census.tracts(refresh=refresh)
    if step in {"build", "all"}:
        from .build import build

        build()
    if step not in {"fetch", "build", "all"}:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
