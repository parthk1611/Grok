"""Exact strings shared with the frontend and the Team Lead rulings (2026-11-09)."""
MANUAL_WEATHER_SOURCE = "manual"
MANUAL_STALE_REASON = "manual weather override; not live data"
# Sources reported by /api/v1/ingestion/status (SourceStatus.source enum in openapi.yaml).
SOURCES = ("hrdps", "cwfis_firewx_stns", "swob_realtime")
