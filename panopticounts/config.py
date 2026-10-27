"""The choices that define the measure. Everything else is plumbing."""

from __future__ import annotations

SOURCE = "panopticounts"
SOURCE_URL = "https://github.com/HarryBrisson/panopticounts"

# How far past the city line to collect plate readers. Every resident is at least this far from the
# edge of the collection area, so any nearest-reader distance up to RING_MILES is exact. The build
# fails if some resident's nearest reader is farther than this.
RING_MILES = 3.0

# "Near a plate reader" = within a quarter mile (about a five-minute walk) of the closest one.
NEAR_MILES = 0.25

# Readers per this many residents.
PER_POPULATION = 10_000

# Flock devices count as plate readers when Proton's inventory says they are in service and read
# plates (falcon, falconFlex, falconHighway and some wing units). Planned and decommissioned
# devices, Raven gunshot detectors and video-only cameras are listed in the summary, not counted.
FLOCK_STATUSES = {"inService"}
FLOCK_PLATE_CAPABILITIES = ["readsLicensePlates", "lpr"]
# Proton sets capability flags on in-service units only; these models are plate readers whatever
# their status, which is how planned and decommissioned ones are recognised for matching.
FLOCK_PLATE_MODELS = {"falcon", "falconFlex", "falconHighway", "lprTrailer"}

# OpenStreetMap has every vendor (Motorola/Vigilant, Genetec, Flock, ...), mapped by volunteers.
# An OSM reader with no manufacturer or a Flock manufacturer tag within MATCH_METERS of a Flock
# plate reader in Proton's inventory is that same device: counted once if Proton has it in service,
# dropped if decommissioned, counted as a later install if it was only planned. Every other OSM
# reader is counted on its own.
OSM_TAG = ("surveillance:type", "ALPR")
MATCH_METERS = 50.0
FLOCK_MANUFACTURERS = {"Flock Safety"}

# Residents: 2020 Census blocks (POP100 at each block's internal point) in Cook County, which holds
# every tract that touches the city.
BLOCK_COUNTIES = [("17", "031")]
