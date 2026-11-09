# T1.0 FIRERESP source revision notes

Ontario Fire Response Plan sectors are downloaded by
`scripts/download_ontario_fire_region.py` (File Geodatabase revision). The
manifest at `data/raw/ontario_fire_region/fire_response_plan_sectors_manifest.json`
records archive and GeoJSON checksums and a **computed** `grid_equivalence`
block: the download script rebuilds the 10 km grid from the written GeoJSON plus
the NFDB event input and compares it to the tracked grid CSV.

## Grid membership and byte-identity

The 10 km grid (`data/interim/ontario_fire_region_10km_grid.csv`, sha256
`b22741d83b64c2b78c19fbf182848566644a23fad4aeef3301ff25ef7c6c3f35`, 9,673 cells)
includes every cell whose centroid lies in the managed Fire Region **and** any
edge cell that contains a selected NFDB ignition even when that cell's centroid
lies outside the Fire Region. Byte-identity of a rebuilt grid therefore depends
on the event input file as well as the sector GeoJSON:

- event input: `data/interim/ontario_nfdb_ignitions_2010_2019.csv`
- event input sha256: `550fe4f7463ca73148dccc140302fca9faaf6803c98fa1edd925c2d42d8fe55c`
