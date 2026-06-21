# Our Neighbourhood Cooling Map

Reproducible starter workflow for the Youth Neighbourhood Small Grants project:
**Our Neighbourhood Cooling Map: Shade, Water, and Places to Rest**.

The script builds an "Existing / Mapped Resources" layer for the Little Mountain /
Riley Park / Cambie / Mount Pleasant area in Vancouver, BC. It is intended for a
simple community-facing map, not a formal heat-risk or planning analysis.

## What This Produces

Run the workflow to create:

- `outputs/cooling_resources_map.html` - interactive Folium web map.
- `outputs/geojson/project_area.geojson`
- `outputs/geojson/project_area_buffer.geojson`
- `outputs/geojson/parks.geojson`
- `outputs/geojson/community_centres.geojson`
- `outputs/geojson/libraries.geojson`
- `outputs/geojson/civic_cooling_places.geojson`
- `outputs/geojson/drinking_fountains.geojson`
- `outputs/geojson/public_washrooms.geojson`
- `outputs/geojson/public_trees.geojson`
- `outputs/geojson/benches_osm.geojson`
- `outputs/geojson/transit_stops_osm.geojson`
- `outputs/geojson/shaded_walking_routes.geojson`
- `outputs/geojson/resident_input.geojson`

Downloaded source files are cached in `data/raw/` so repeated runs are faster.

## Data Sources

City of Vancouver Open Data:

- Parks - polygon representation: `parks-polygon-representation`
- Community centres: `community-centres`
- Libraries: `libraries`
- Drinking fountains: `drinking-fountains`
- Public washrooms: `public-washrooms`
- Public trees: `public-trees`
- Rapid transit stations: `rapid-transit-stations`
- Local area boundary: `local-area-boundary`

OpenStreetMap, via OSMnx:

- Benches: `amenity=bench`
- Transit stops: `highway=bus_stop`, `public_transport=platform`, and related
  transit tags
- Walking network for the shade-route proxy

## Project Area

The default boundary is the union of City local areas:

- Mount Pleasant
- Riley Park
- South Cambie

The script then creates a 500 metre buffer for "nearby" resources. This is a
practical approximation for a community map around Little Mountain, Riley Park,
Cambie, and Mount Pleasant. Edit `LOCAL_AREAS` and `BUFFER_METRES` in
`scripts/build_existing_resources.py` if you want a tighter or broader area.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

```powershell
python scripts/build_existing_resources.py
```

Open `outputs/cooling_resources_map.html` in a browser.

## Public Display Modes

The HTML map includes a public-facing introduction panel, a plain-language
legend, and four display modes:

- `Standard` - regular public map view.
- `Large text` - larger labels, popups, and markers for older adults or people
  who prefer easier reading.
- `High contrast` - stronger outlines, text labels on markers, and a
  colorblind-friendly palette. The map does not rely on color alone.
- `Simple view` - hides technical notes and extra layer controls for a calmer
  display at a workshop, library, school, or community centre.

Markers use short text labels such as `COOL`, `WATER`, `WC`, `SIT`, and `BUS`
so residents do not need GIS knowledge or a color legend to understand the map.

The same panel also includes scenario modes:

- `All` - shows all resource types.
- `Senior` - emphasizes places to sit, washrooms, water, and civic cooling
  places.
- `Family` - emphasizes water, washrooms, parks, and civic buildings.
- `Extreme heat` - emphasizes cooling places, water, washrooms, and transit.
- `Resident input` - emphasizes questionnaire and workshop feedback.

Map wording note:

- Light green filled areas are parks and green spaces. They are not a guarantee
  of shade.
- Dark or dashed green lines are `Possible shady walking routes`. These are
  estimated from nearby public street trees, so they should be checked with
  resident feedback and field observation.
- `TRAIN` markers mean SkyTrain or rapid transit stations.
- Residents can use the `Search park name...` box on the map if they know a
  park name but are not comfortable reading the map directly.

## Add Resident Feedback

Use `data/resident_input_template.csv` for questionnaire and workshop points.
Each row should be one place, comment, or suggestion. Required fields:

- `name` - short public-facing name.
- `feedback_type` - use values such as `good_rest_spot`, `hot_spot`,
  `needs_improvement`, `water_feedback`, `shade_feedback`, or
  `accessibility_feedback`.
- `description` - what residents said about the place.
- `best_for` - who this point may matter for, such as `seniors`, `families`,
  `walkers`, or `everyone`.
- `latitude` and `longitude` - point coordinates in WGS84 decimal degrees.
- `source` - for example `questionnaire`, `workshop`, or `field check`.
- `verified` - `true` or `false`.

After editing the CSV, rerun:

```powershell
python scripts/build_existing_resources.py
```

The script will create `outputs/geojson/resident_input.geojson` and add the
resident layer to the HTML map.

## Manual Downloads

The workflow should run without manual downloads. If an API call fails, manually
download GeoJSON from the City of Vancouver Open Data page for that dataset and
place it in `data/raw/` using the file names shown in the script, for example:

- `data/raw/parks-polygon-representation.geojson`
- `data/raw/drinking-fountains.geojson`
- `data/raw/public-trees.geojson`

Then rerun the script.

## Notes And Limits

- "Civic cooling places" are a practical starting layer made from community
  centres and libraries. During a heat event, verify official cooling-centre
  operations, hours, and accessibility with the City or facility websites.
- The public map is designed to be friendly and readable, but it is not a
  substitute for emergency information during an extreme heat event.
- The shaded walking routes layer is a rough public-tree-density proxy, not a
  measured shade/canopy model. It does not account for tree height, sun angle,
  private trees, awnings, construction, or route comfort.
- OSM benches and bus stops depend on community-contributed mapping and may be
  incomplete.
- Add questionnaire and workshop responses as new GeoJSON/CSV layers rather than
  editing the source layers directly.
