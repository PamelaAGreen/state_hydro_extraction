# State Hydro and Flood Data Extraction

Reusable Python extractors and an interactive notebook for downloading, standardizing, and caching hydrographic, flood-hazard, flood-impact, land-cover, structure, and reference-geography data for a selected U.S. state.

The repository supports two ways of running the extractors:

- **Notebook mode:** Run `notebooks/ExtractByStates.ipynb` and set the state configuration once.
- **Headless CLI mode:** Run an individual script from the command line.

The project is configured to run **Rhode Island** by default:

```text
State FIPS: 44
State abbreviation: RI
State name: Rhode Island
Year range: 2010–2024
Land-cover year: 2024
```

Outputs are written to `data/raw/` by default. Most extractors reuse an existing output file unless `--force` is supplied.

## Why I built this

I use many of these datasets in my exploration and model building. I wanted to have a library of code snippets I could pull from to download the latest data without having to re-invent the wheel or go searching through older code or  repos.

## Included sources

### `usgs_stream_gauges.py`

Retrieves USGS National Water Information System stream sites that have discharge or gauge-height record history.

**Output:** `usgs_stream_gauges_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/usgs_stream_gauges.py
```

**Specify state**

```bash
python src/usgs_stream_gauges.py MA
```

### `usgs_wbd_huc12_subwatersheds.py`

Retrieves USGS Watershed Boundary Dataset HUC12 subwatersheds whose `States` attribute includes the selected state. It retains complete, un-clipped HUC12 polygons, including portions extending beyond the state boundary.

**Output:** `usgs_wbd_huc12_subwatersheds_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/usgs_wbd_huc12_subwatersheds.py
```

**Specify state**

```bash
python src/usgs_wbd_huc12_subwatersheds.py MA
```

### `extract_nfhl_msc.py`

Downloads FEMA’s current effective National Flood Hazard Layer state package from the FEMA Map Service Center, extracts the flood-hazard-area feature class, and saves the result as GeoParquet.

**Output:** `nfhl_flood_zones_<STATE>_MSC.parquet`

**Headless CLI commands**

**Default**

```bash
python src/extract_nfhl_msc.py
```

**Specify state**

Set the target state in the project configuration used by `extract_nfhl_msc.py`, then run:

```bash
python src/extract_nfhl_msc.py
```

> This extractor currently reads `STATE_FIPS` and `STATE_ABBR` from `extract.py`; it does not yet accept state values as command-line arguments. [file:228]

### `usace_nsi_structures.py`

Retrieves point-level records from the USACE National Structure Inventory. The extractor uses the Census county-boundary extractor to obtain county FIPS codes and requests NSI records county by county.

**Output:** `nsi_structures_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/usace_nsi_structures.py
```

**Specify state**

```bash
python src/usace_nsi_structures.py 25 MA
```

### `noaa_storm_events.py`

Retrieves NOAA Storm Events Database records for Flood, Flash Flood, Coastal Flood, and Lakeshore Flood events. County records are assigned directly to county FIPS; forecast-zone records are expanded through NOAA/NWS zone-to-county correlation data.

**Output:** `noaa_storm_events_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/noaa_storm_events.py
```

**Specify state**

```bash
python src/noaa_storm_events.py 25 MA Massachusetts 2010 2024
```

### `openfema_nfip_claims.py`

Retrieves OpenFEMA National Flood Insurance Program redacted claims for the selected state and inclusive date range.

**Output:** `nfip_claims_<STATE>_<START_YEAR>_<END_YEAR>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/openfema_nfip_claims.py
```

**Specify state**

```bash
python src/openfema_nfip_claims.py MA 2010 2024
```

### `census_counties.py`

Downloads Census TIGER/Line county boundaries and filters the nationwide file to the selected state.

**Output:** `counties_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/census_counties.py
```

**Specify state**

```bash
python src/census_counties.py 25 MA
```

### `census_county_subdivisions.py`

Downloads Census TIGER/Line county-subdivision boundaries for the selected state. County subdivisions are Census geographic units; their relationship to local government varies by state.

**Output:** `county_subdivisions_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/census_county_subdivisions.py
```

**Specify state**

```bash
python src/census_county_subdivisions.py 25 MA
```

### `census_tract_urban_rural.py`

Combines Census TIGER/Line tract polygons with 2020 Decennial Census DHC table H2 values for total, urban, and rural housing units. The output includes `pct_urban` and `pct_rural`.

***NOTE: You will need a Census API to run this code; see directions below Installation***

**Output:** `census_tract_urban_rural_<STATE>.parquet`

**Headless CLI commands**

**Default**

```bash
python src/census_tract_urban_rural.py
```

**Specify state**

```bash
python src/census_tract_urban_rural.py 25 MA
```

### `mrlc_land_cover.py`

Downloads an Annual National Land Cover Database GeoTIFF from the MRLC WMS for the selected state’s bounding box. The resulting raster is not clipped to the precise state boundary.

**Output:** `mrlc_land_cover_<STATE>_<YEAR>_bbox.tif`

**Headless CLI commands**

**Default**

```bash
python src/mrlc_land_cover.py
```

**Specify state**

```bash
python src/mrlc_land_cover.py 25 MA 2024
```

## Repository layout

```text
project-root/
├── data/
│   └── raw/                  # Downloaded outputs; excluded from Git
├── notebooks/
│   └── ExtractByStates.ipynb # Interactive state-level runner
├── src/
│   ├── census_counties.py
│   ├── census_county_subdivisions.py
│   ├── census_tract_urban_rural.py
│   ├── extract_nfhl_msc.py
│   ├── mrlc_land_cover.py
│   ├── noaa_storm_events.py
│   ├── openfema_nfip_claims.py
│   ├── usace_nsi_structures.py
│   ├── usgs_stream_gauges.py
│   └── usgs_wbd_huc12_subwatersheds.py
├── .env                      # Local credentials; excluded from Git
└── requirements.txt
```

## Installation

Create and activate a Python environment, then install the project dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The extractors use packages including `geopandas`, `pandas`, `requests`, `pyarrow`, `rasterio`, `python-dotenv`, and `dataretrieval`.

## Census API key

The tract urban/rural extractor requires a Census API key. Store it in a project-root `.env` file:

```text
CENSUS_API_KEY=your_census_api_key_here
```

Do not commit `.env` or expose the key in a notebook, script, or public repository.

## Notebook use

Open and run:

```text
notebooks/ExtractByStates.ipynb
```

In the **Setup** cell, edit the state variables. For example:

```python
STATE_FIPS = "44"
STATE_ABBR = "RI"
STATE_NAME = "Rhode Island"

YEAR_START = 2010
YEAR_END = 2024
LAND_COVER_YEAR = 2024
```

For Massachusetts:

```python
STATE_FIPS = "25"
STATE_ABBR = "MA"
STATE_NAME = "Massachusetts"
```

Run the Setup cell first, then run only the extractor sections needed for the selected state.

## Common options

Most scripts support the following options:

```bash
--output-dir PATH
--force
```

- `--output-dir PATH` changes the folder where downloaded outputs are saved.
- `--force` replaces an existing cached output.
- `--tiger-year YEAR` changes the TIGER/Line boundary vintage for applicable Census-based extractors.
- `--help` displays the exact options accepted by an individual script.

For example:

```bash
python src/usgs_wbd_huc12_subwatersheds.py --help
```

## Output format and CRS

Vector outputs use Parquet or GeoParquet format and are standardized to geographic coordinates in EPSG:4326 where applicable. The land-cover output is a GeoTIFF raster.

Temporary directories created during archive extraction may appear under `data/raw/`. They can be removed after confirming the final output file was saved successfully.

## Caching and refreshes

Each extractor checks whether its expected output already exists. If it does, the saved file is returned instead of downloading the source again.

To refresh a dataset:

```bash
python src/usgs_wbd_huc12_subwatersheds.py --force
```

Use `--force` deliberately because source datasets can be large, APIs can rate-limit requests, and a refresh overwrites the local cached output.

## Data-use notes

These scripts retrieve public data from their respective agencies. Users are responsible for reviewing each provider’s documentation, license, update schedule, attribute definitions, and appropriate-use guidance before distributing or interpreting the data.

The repository stores extraction code only. Downloaded files in `data/raw/`, temporary archive contents, and local credentials should remain outside version control.

## License

This prototype is licensed under the MIT License so that others can freely use, adapt, and integrate it into their own workflows. As a courtesy, please avoid reselling the repository as-is without meaningful changes, and always retain clear attribution to the original author in derivative work. Contributions and improvements are welcome.