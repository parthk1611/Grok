# COMP-385

Ontario wildfire ignition modelling data for COMP-385.

The established baseline is a 2010–2019 Ontario Fire Region 10 km fire-season
cell/day ignition label panel. The large label and feature panels are
reproducible build artifacts, while their compact inputs, manifests, audits, and
the v1 feature implementation are tracked. The feature build adds time-bounded
calendar, static CDEM terrain, prior-day CWFIS weather/FWI, and past-only
ignition-history features without changing that panel's row universe.

See [the ignition feature documentation](docs/ignition_features.md) for source
provenance, leakage boundaries, deferred feature families, and reproducible
build commands.

The second AI capability, wind-driven fire spread, has its own folder:
[`capability_2_fire_spread/`](capability_2_fire_spread/README.md). It holds a
separate dataset of 752 Ontario fires from 2012–2025 with their growth rebuilt
from satellite passes, hourly wind and fire-weather codes, fuel and terrain.
It is a replay set for the spread simulator and is never an ignition input.
