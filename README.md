# panopticounts

How many license-plate-reading cameras (ALPRs) watch each part of Chicago, and how close residents
live to one: a [byop/v1](https://github.com/ward-wise/ward-wise-measures) measure source for
[Penlight](https://penlight.wardwise.org).

It publishes **aggregates only**: one row per ward, per community area and per 2020 census tract.
No camera, coordinate or block-level row is in the release.

## Sources

- **OpenStreetMap**: every node tagged `surveillance:type=ALPR`, all vendors (Motorola/Vigilant,
  Genetec, Flock, ...), mapped by volunteers. This is the data [DeFlock](https://deflock.org) maps.
  One Overpass query over the city plus a 3-mile ring. © OpenStreetMap contributors, ODbL.
- **Flock Safety's device inventory**: the file behind
  [Proton's Flock camera map](https://proton.me/flock-camera-map), a December 2025 snapshot that
  Proton credits to researcher Joshua Michael, who obtained it through an access token Flock's
  servers exposed. It lists each device's model, status and capabilities. The file has no license,
  so this repo never redistributes it: `fetch` downloads it into the gitignored `data/raw/`, and only
  counts and distances derived from it are published.

## Method

1. **Flock plate readers**: from the inventory, devices in service that read plates (`falcon`,
   `falconFlex`, `falconHighway`, plate-reading `wing` units). Raven gunshot detectors, video-only
   cameras and planned or decommissioned devices are tallied in `summary.json`, not counted.
2. **Merge with OSM**: an OSM reader tagged Flock or untagged that lies within 50 m of a Flock plate
   reader is treated as that device:
   - If Proton lists it as in service, it's counted once.
   - If Proton lists it as decommissioned, it isn't counted (the OSM node is stale).
   - If it was only planned in December 2025, it's counted as installed since then.

   Every other OSM reader counts on its own.
3. **Residents**: the 2020 Census count at each populated block's internal point. Distances are straight
   lines to the nearest counted reader, including readers up to 3 miles outside the city. The
   build fails if any resident's nearest reader could be beyond that ring.
4. **Roll-ups**:
   - Wards (2023 map) and community areas are computed directly from blocks and readers.
   - Tracts at least 1% inside the city get whole-tract values.
   - Penlight reads wards and community areas from their own tables. Anything else, such as its
     χGRID cells, it takes from the overlapping tracts (`"layer": ["wards", "community_areas", "tracts"]`
     plus each layer's `geography`).

| metric | layer value | weight | direction |
|---|---|---|---|
| `alpr_per_sqkm` | readers per km² | area | lower |
| `alpr_per_10k_residents` | readers per 10,000 residents | population | lower |
| `residents_near_alpr_pct` | % of residents within ¼ mile of a reader | population | lower |
| `alpr_nearest_mi` | mean miles from residents to the nearest reader | population | higher |
| `flock_alpr_per_sqkm` | Flock readers per km² | area | lower |

**Direction is a values call.** Fewer readers is scored as better by default, a privacy reading; others
count the same cameras as a public-safety investment. Every metric therefore ships with
`"direction_contested": true` (byop v1.4: a consumer lets each reader flip it, and Penlight shows a
"lower is better / higher is better" switch) and `default_weight: 0`, and the methodology text says so.

## What the numbers say (OSM 2026-10-27, Flock inventory Dec 2025)

- **Citywide**:
  - 814 plate readers: 1.36 per km², 3.0 per 10,000 residents.
  - 40% of Chicagoans live within a quarter mile of one; the average resident is 0.36 miles from the nearest.
- **By vendor**: of the city's readers, 135 are Flock, 258 Motorola, 54 Genetec and 357 untagged in OSM.
  OSM tags 224 as Chicago Police and 58 as Illinois State Police (expressways).
- **Densest**: Ward 42 (10.2 per km²), Fuller Park (12.5, the Dan Ryan) and the Loop (8.1).
  Near South Side, West Garfield Park and Hyde Park each have 87–95% of residents within a quarter mile.
- **Sparsest**: Ward 49 and five community areas (Oakland, South Shore, Calumet Heights, East Side,
  Clearing) have no reader on the map.

**Coverage caveat**: non-Flock readers exist here only if a volunteer mapped them. Flock's inventory is
the one check available. OSM had mapped 65% of Flock's in-service readers in the city: 74% north of
Madison Street and 57% south. Non-Flock readers are therefore undercounted, more so on the South Side,
and a zero is not proof of absence. The spec's `caveat` carries the measured numbers on every build.

## Run it

```bash
pip install -e .
python -m panopticounts fetch        # Proton's file, OSM, 2020 blocks + tracts -> data/raw (cached)
python -m panopticounts build        # data/layers/*.csv + aggregation_spec.json, data/processed/summary.json
python -m panopticounts fetch --refresh && python -m panopticounts build   # a fresh OSM pull / Proton build
```

Validate and publish with the registry's tool (`byop validate data/layers`, then `byop publish`).

## Licenses

Code: MIT. Data in `data/layers/` and `data/processed/`: derived in part from OpenStreetMap, so it is
available under the [ODbL](https://opendatacommons.org/licenses/odbl/), © OpenStreetMap contributors.
