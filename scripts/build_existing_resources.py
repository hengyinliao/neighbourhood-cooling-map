"""
Build the existing / mapped resources layer for a community cooling map.

The workflow:
1. Defines the project area from City of Vancouver local area boundaries.
2. Downloads and caches public open-data layers.
3. Cleans common fields into a simple schema.
4. Clips resources to the buffered project area.
5. Builds a rough shaded-walking-route proxy from public trees near OSM walking
   network segments.
6. Exports GeoJSON layers and an interactive Folium HTML map.

This is designed for a community-facing resource map, not a formal planning
analysis.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Iterable

import folium
import geopandas as gpd
import pandas as pd
import requests
from folium.plugins import MarkerCluster, Search
from shapely.geometry import Point

try:
    import osmnx as ox
except ImportError:  # pragma: no cover - handled gracefully for users
    ox = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "outputs"
GEOJSON_DIR = OUTPUT_DIR / "geojson"
SITE_DIR = ROOT / "docs"
RESIDENT_INPUT_CSV = DATA_DIR / "resident_input_template.csv"
PUBLIC_MAP_TITLE = "Our Neighbourhood Cooling Map"

VANCOUVER_OPEN_DATA_EXPORT = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "{dataset}/exports/geojson?lang=en&timezone=America%2FVancouver"
)

LOCAL_AREAS = ["Mount Pleasant", "Riley Park", "South Cambie"]
BUFFER_METRES = 500

CRS_WGS84 = "EPSG:4326"
CRS_LOCAL_METRES = "EPSG:26910"  # NAD83 / UTM zone 10N, suitable for Vancouver.


CITY_DATASETS = {
    "local_area_boundary": "local-area-boundary",
    "parks": "parks-polygon-representation",
    "community_centres": "community-centres",
    "libraries": "libraries",
    "drinking_fountains": "drinking-fountains",
    "public_washrooms": "public-washrooms",
    "public_trees": "public-trees",
    "rapid_transit_stations": "rapid-transit-stations",
}

POINT_STYLES = {
    "cooling": {
        "label": "COOL",
        "class": "cooling-marker",
        "popup_type": "Cooling place",
        "plain_help": "A community centre or library that may be useful during hot weather. Check current opening hours before going.",
    },
    "water": {
        "label": "WATER",
        "class": "water-marker",
        "popup_type": "Water",
        "plain_help": "A public drinking fountain or bottle filling location.",
    },
    "washroom": {
        "label": "WC",
        "class": "washroom-marker",
        "popup_type": "Washroom",
        "plain_help": "A public washroom. Hours and access may change by season.",
    },
    "bench": {
        "label": "SIT",
        "class": "bench-marker",
        "popup_type": "Place to sit",
        "plain_help": "A mapped bench or place to sit from OpenStreetMap.",
    },
    "bus": {
        "label": "BUS",
        "class": "transit-marker",
        "popup_type": "Transit stop",
        "plain_help": "A mapped bus or transit stop from OpenStreetMap.",
    },
    "train": {
        "label": "TRAIN",
        "class": "rapid-marker",
        "popup_type": "Train or rapid transit station",
        "plain_help": "A SkyTrain or rapid transit station.",
    },
    "resident_good": {
        "label": "GOOD",
        "class": "resident-good-marker",
        "popup_type": "Resident-recommended place",
        "plain_help": "A place residents identified as useful, comfortable, or worth knowing about.",
    },
    "resident_hot": {
        "label": "HOT",
        "class": "resident-hot-marker",
        "popup_type": "Resident-identified hot spot",
        "plain_help": "A place residents identified as hot, uncomfortable, or needing attention.",
    },
    "resident_need": {
        "label": "NEED",
        "class": "resident-need-marker",
        "popup_type": "Resident suggestion",
        "plain_help": "A place residents suggested for improvement, clearer information, or future action.",
    },
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)


def download_city_geojson(dataset: str, refresh: bool = False) -> Path:
    """Download a City of Vancouver GeoJSON export unless already cached."""
    out_path = RAW_DIR / f"{dataset}.geojson"
    if out_path.exists() and not refresh:
        return out_path

    url = VANCOUVER_OPEN_DATA_EXPORT.format(dataset=dataset)
    print(f"Downloading {dataset}...")
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    # Validate before writing so a portal error page does not become cached data.
    payload = response.json()
    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"Unexpected response for {dataset}: {payload.keys()}")
    out_path.write_text(json.dumps(payload), encoding="utf-8")
    return out_path


def read_city_layer(key: str) -> gpd.GeoDataFrame:
    path = download_city_geojson(CITY_DATASETS[key])
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_WGS84)
    return gdf.to_crs(CRS_WGS84)


def export_geojson(gdf: gpd.GeoDataFrame, name: str) -> Path:
    out = GEOJSON_DIR / f"{name}.geojson"
    gdf = make_properties_json_safe(gdf)
    if gdf.empty:
        # Keep an empty file with the expected CRS/schema for reproducibility.
        gdf.to_file(out, driver="GeoJSON")
    else:
        gdf.to_crs(CRS_WGS84).to_file(out, driver="GeoJSON")
    return out


def build_project_area() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    local_areas = read_city_layer("local_area_boundary")
    selected = local_areas[local_areas["name"].isin(LOCAL_AREAS)].copy()
    missing = sorted(set(LOCAL_AREAS) - set(selected["name"]))
    if missing:
        raise ValueError(f"Missing local areas in source data: {missing}")

    area = selected.to_crs(CRS_LOCAL_METRES).dissolve()
    area["name"] = " / ".join(LOCAL_AREAS)
    area["layer"] = "project_area"
    area = area[["name", "layer", "geometry"]].to_crs(CRS_WGS84)

    buffered = area.to_crs(CRS_LOCAL_METRES).copy()
    buffered["geometry"] = buffered.geometry.buffer(BUFFER_METRES)
    buffered["name"] = f"{area.iloc[0]['name']} + {BUFFER_METRES} m"
    buffered["layer"] = "project_area_buffer"
    buffered = buffered.to_crs(CRS_WGS84)
    return area, buffered


def clip_to_area(gdf: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    gdf = gdf[gdf.geometry.notna()].copy()
    if gdf.empty:
        return gdf
    return gpd.clip(gdf.to_crs(CRS_LOCAL_METRES), boundary.to_crs(CRS_LOCAL_METRES)).to_crs(CRS_WGS84)


def first_existing_column(gdf: gpd.GeoDataFrame, candidates: Iterable[str]) -> str | None:
    lowered = {c.lower(): c for c in gdf.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def json_safe_value(value):
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        if isinstance(converted, list):
            return ", ".join(str(item) for item in converted)
        return converted
    return value


def make_properties_json_safe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Convert list-like attribute values so Folium and GeoJSON exports stay simple."""
    if gdf.empty:
        return gdf
    out = gdf.copy()
    for col in out.columns:
        if col == out.geometry.name:
            continue
        out[col] = out[col].map(json_safe_value)
    return out


def standardize(
    gdf: gpd.GeoDataFrame,
    layer: str,
    category: str,
    source: str,
    name_candidates: Iterable[str] = ("name", "mapid", "facility_name", "address"),
    keep_columns: Iterable[str] = (),
) -> gpd.GeoDataFrame:
    """Create a small common schema while preserving selected source fields."""
    gdf = gdf.copy()
    name_col = first_existing_column(gdf, name_candidates)
    if name_col:
        names = gdf[name_col].map(json_safe_value).fillna(category).astype(str)
    else:
        names = category

    out = gpd.GeoDataFrame(
        {
            "name": names,
            "category": category,
            "layer": layer,
            "source": source,
        },
        geometry=gdf.geometry,
        crs=gdf.crs,
    )

    for col in keep_columns:
        if col in gdf.columns:
            out[col] = gdf[col]
    return out


def combine_layers(layers: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    layers = [layer for layer in layers if not layer.empty]
    if not layers:
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)
    return gpd.GeoDataFrame(
        pd.concat(layers, ignore_index=True),
        crs=layers[0].crs,
    ).to_crs(CRS_WGS84)


def load_city_resources(buffered_area: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    city_source = "City of Vancouver Open Data"
    layers: dict[str, gpd.GeoDataFrame] = {}

    parks = clip_to_area(read_city_layer("parks"), buffered_area)
    layers["parks"] = standardize(
        parks,
        layer="parks",
        category="Park or green space",
        source=city_source,
        name_candidates=("park_name", "name", "mapid"),
        keep_columns=("park_name", "area_ha"),
    )

    community_centres = clip_to_area(read_city_layer("community_centres"), buffered_area)
    layers["community_centres"] = standardize(
        community_centres,
        layer="community_centres",
        category="Community centre",
        source=city_source,
        name_candidates=("name", "address"),
        keep_columns=("address", "url"),
    )

    libraries = clip_to_area(read_city_layer("libraries"), buffered_area)
    layers["libraries"] = standardize(
        libraries,
        layer="libraries",
        category="Library",
        source=city_source,
        name_candidates=("name", "address"),
        keep_columns=("address", "url"),
    )

    layers["civic_cooling_places"] = combine_layers(
        [
            layers["community_centres"].assign(category="Potential civic cooling place"),
            layers["libraries"].assign(category="Potential civic cooling place"),
        ]
    )

    fountains = clip_to_area(read_city_layer("drinking_fountains"), buffered_area)
    layers["drinking_fountains"] = standardize(
        fountains,
        layer="drinking_fountains",
        category="Drinking fountain or bottle fill",
        source=city_source,
        name_candidates=("location", "name", "address"),
        keep_columns=("location", "in_operation", "maintainer"),
    )

    washrooms = clip_to_area(read_city_layer("public_washrooms"), buffered_area)
    layers["public_washrooms"] = standardize(
        washrooms,
        layer="public_washrooms",
        category="Public washroom",
        source=city_source,
        name_candidates=("name", "location", "address"),
        keep_columns=("location", "summer_hours", "winter_hours", "wheelchair_accessible"),
    )

    trees = clip_to_area(read_city_layer("public_trees"), buffered_area)
    layers["public_trees"] = standardize(
        trees,
        layer="public_trees",
        category="Public tree",
        source=city_source,
        name_candidates=("common_name", "species_name", "genus_name"),
        keep_columns=("common_name", "diameter", "on_street", "neighbourhood_name"),
    )

    rapid = clip_to_area(read_city_layer("rapid_transit_stations"), buffered_area)
    layers["rapid_transit_stations"] = standardize(
        rapid,
        layer="rapid_transit_stations",
        category="Rapid transit station",
        source=city_source,
        name_candidates=("station", "name"),
        keep_columns=("line",),
    )

    return layers


def load_resident_input(buffered_area: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Load questionnaire or workshop points from the resident input CSV."""
    columns = [
        "name",
        "feedback_type",
        "description",
        "best_for",
        "latitude",
        "longitude",
        "source",
        "verified",
        "contact_or_session",
        "notes",
        "category",
        "layer",
    ]
    empty = gpd.GeoDataFrame(columns=columns + ["geometry"], crs=CRS_WGS84)
    if not RESIDENT_INPUT_CSV.exists():
        return empty

    df = pd.read_csv(RESIDENT_INPUT_CSV)
    required = {"latitude", "longitude"}
    if not required.issubset(df.columns):
        print(f"Skipping resident input: {RESIDENT_INPUT_CSV} needs latitude and longitude columns.")
        return empty

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"]).copy()
    if df.empty:
        return empty

    for col in columns:
        if col not in df.columns:
            df[col] = ""

    type_labels = {
        "good_rest_spot": "Resident recommended rest or cooling spot",
        "hot_spot": "Resident identified hot spot",
        "needs_improvement": "Resident suggestion or improvement needed",
        "water_feedback": "Resident water access feedback",
        "shade_feedback": "Resident shade feedback",
        "accessibility_feedback": "Resident accessibility feedback",
    }
    df["feedback_type"] = df["feedback_type"].fillna("resident_feedback").astype(str).str.strip()
    df["category"] = df["feedback_type"].map(type_labels).fillna("Resident feedback")
    df["layer"] = "resident_input"
    df["source"] = df["source"].fillna("Resident input").replace("", "Resident input")

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=CRS_WGS84,
    )
    return clip_to_area(gdf, buffered_area)


def resident_style_key(row: pd.Series) -> str:
    feedback_type = str(row.get("feedback_type", "")).lower()
    if "hot" in feedback_type:
        return "resident_hot"
    if "need" in feedback_type or "improvement" in feedback_type:
        return "resident_need"
    return "resident_good"


def osm_features(boundary: gpd.GeoDataFrame, tags: dict, layer: str, category: str) -> gpd.GeoDataFrame:
    cache_path = RAW_DIR / f"{layer}.geojson"
    if cache_path.exists():
        return gpd.read_file(cache_path).to_crs(CRS_WGS84)

    if ox is None:
        print(f"Skipping {layer}: osmnx is not installed.")
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    polygon = boundary.to_crs(CRS_WGS84).geometry.iloc[0]
    print(f"Downloading OSM {layer}...")
    try:
        gdf = ox.features_from_polygon(polygon, tags=tags)
    except Exception as exc:
        print(f"Skipping {layer}: OSM request failed ({exc}).")
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    if gdf.empty:
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    gdf = gdf.reset_index()
    # Convert polygons/lines for amenities to representative points for a simple map.
    point_geom = gdf.to_crs(CRS_LOCAL_METRES).geometry.representative_point().to_crs(CRS_WGS84)
    gdf = gdf.set_geometry(point_geom).set_crs(CRS_WGS84, allow_override=True)
    out = standardize(
        gdf,
        layer=layer,
        category=category,
        source="OpenStreetMap",
        name_candidates=("name", "ref", "operator"),
        keep_columns=("amenity", "highway", "public_transport", "operator", "network"),
    )
    out.to_file(cache_path, driver="GeoJSON")
    return out


def build_shaded_routes(boundary: gpd.GeoDataFrame, trees: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Approximate shaded walking routes by counting public trees near street edges."""
    cache_path = RAW_DIR / "shaded_walking_routes.geojson"
    if cache_path.exists():
        return gpd.read_file(cache_path).to_crs(CRS_WGS84)

    if ox is None:
        print("Skipping shaded routes: osmnx is not installed.")
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)
    if trees.empty:
        print("Skipping shaded routes: no public trees found in project buffer.")
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    polygon = boundary.to_crs(CRS_WGS84).geometry.iloc[0]
    print("Downloading OSM walking network for shaded-route proxy...")
    try:
        graph = ox.graph_from_polygon(polygon, network_type="walk", simplify=True)
        _, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    except Exception as exc:
        print(f"Skipping shaded routes: OSM network request failed ({exc}).")
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    if edges.empty:
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    edges = edges.reset_index().to_crs(CRS_LOCAL_METRES)
    edges["length_m"] = edges.geometry.length
    edges = edges[edges["length_m"] >= 25].copy()
    edges["edge_id"] = range(len(edges))

    tree_points = trees.to_crs(CRS_LOCAL_METRES)[["geometry"]].copy()
    buffered_edges = edges[["edge_id", "geometry"]].copy()
    buffered_edges["geometry"] = buffered_edges.geometry.buffer(12)

    joined = gpd.sjoin(tree_points, buffered_edges, predicate="within", how="inner")
    counts = joined.groupby("edge_id").size().rename("tree_count")
    edges = edges.join(counts, on="edge_id")
    edges["tree_count"] = edges["tree_count"].fillna(0).astype(int)
    edges["trees_per_100m"] = edges["tree_count"] / edges["length_m"] * 100

    def shade_class(value: float) -> str:
        if value >= 6:
            return "Higher shade potential"
        if value >= 3:
            return "Moderate shade potential"
        return "Lower shade potential"

    edges["category"] = edges["trees_per_100m"].apply(shade_class)
    edges = edges[edges["category"] != "Lower shade potential"].copy()
    if edges.empty:
        return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)

    name_col = first_existing_column(edges, ("name", "ref"))
    names = edges[name_col].fillna("Walking route segment").astype(str) if name_col else "Walking route segment"
    out = gpd.GeoDataFrame(
        {
            "name": names,
            "category": edges["category"],
            "layer": "shaded_walking_routes",
            "source": "OpenStreetMap walking network + City public trees",
            "length_m": edges["length_m"].round(1),
            "tree_count": edges["tree_count"],
            "trees_per_100m": edges["trees_per_100m"].round(2),
        },
        geometry=edges.geometry,
        crs=CRS_LOCAL_METRES,
    )
    out = out.to_crs(CRS_WGS84)
    out.to_file(cache_path, driver="GeoJSON")
    return out


def popup_html(row: pd.Series, plain_type: str, plain_help: str) -> str:
    title = escape(str(row.get("name", plain_type)))
    category = escape(str(row.get("category", plain_type)))
    source = escape(str(row.get("source", "Open data")))
    help_text = escape(plain_help)
    extra_lines = []
    for label, field in [
        ("Resident note", "description"),
        ("Good for", "best_for"),
        ("Verified", "verified"),
        ("Session", "contact_or_session"),
    ]:
        value = row.get(field, "")
        if pd.notna(value) and str(value).strip():
            extra_lines.append(f"<div><b>{label}:</b> {escape(str(value))}</div>")
    extra_html = "\n".join(extra_lines)
    return f"""
    <div class="resource-popup">
      <div class="popup-title">{title}</div>
      <div><b>What it is:</b> {category}</div>
      <div><b>Helpful note:</b> {help_text}</div>
      {extra_html}
      <div class="popup-source">Source: {source}</div>
    </div>
    """


def marker_icon(style_key: str) -> folium.DivIcon:
    style = POINT_STYLES[style_key]
    return folium.DivIcon(
        html=(
            f'<div class="cooling-map-marker {style["class"]}" '
            f'aria-label="{escape(style["popup_type"])}">{style["label"]}</div>'
        ),
        icon_size=(46, 46),
        icon_anchor=(23, 23),
        popup_anchor=(0, -20),
        class_name="cooling-div-icon",
    )


def add_point_layer(
    fmap: folium.Map,
    gdf: gpd.GeoDataFrame,
    name: str,
    style_key: str,
    cluster: bool = False,
) -> None:
    if gdf.empty:
        return

    style = POINT_STYLES[style_key]
    target = MarkerCluster(name=name) if cluster else folium.FeatureGroup(name=name)
    for _, row in gdf.to_crs(CRS_WGS84).iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        point = geom if isinstance(geom, Point) else geom.representative_point()
        folium.Marker(
            location=[point.y, point.x],
            popup=folium.Popup(popup_html(row, style["popup_type"], style["plain_help"]), max_width=360),
            tooltip=row.get("name", name),
            icon=marker_icon(style_key),
        ).add_to(target)
    target.add_to(fmap)


def add_resident_input_layer(fmap: folium.Map, gdf: gpd.GeoDataFrame) -> None:
    if gdf.empty:
        return

    target = folium.FeatureGroup(name="Resident feedback and workshop notes")
    for _, row in gdf.to_crs(CRS_WGS84).iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        point = geom if isinstance(geom, Point) else geom.representative_point()
        style_key = resident_style_key(row)
        style = POINT_STYLES[style_key]
        folium.Marker(
            location=[point.y, point.x],
            popup=folium.Popup(popup_html(row, style["popup_type"], style["plain_help"]), max_width=380),
            tooltip=row.get("name", "Resident feedback"),
            icon=marker_icon(style_key),
        ).add_to(target)
    target.add_to(fmap)


def add_public_accessibility_controls(fmap: folium.Map) -> None:
    """Add public-facing guidance and visual modes to the generated HTML map."""
    css = """
    <style>
      :root {
        --panel-bg: #ffffff;
        --panel-text: #17202a;
        --panel-border: #8a8f98;
        --focus-ring: #111111;
        --cooling: #0072b2;
        --water: #009e73;
        --washroom: #cc79a7;
        --bench: #e69f00;
        --transit: #d55e00;
        --rapid: #000000;
      }
      body.access-senior {
        font-size: 19px;
      }
      body.access-colorblind {
        --cooling: #0072b2;
        --water: #009e73;
        --washroom: #cc79a7;
        --bench: #f0e442;
        --transit: #d55e00;
        --rapid: #000000;
      }
      body.access-simple .leaflet-control-layers,
      body.access-simple .technical-note {
        display: none !important;
      }
      .map-intro-panel,
      .map-mode-panel,
      .map-legend-panel {
        position: fixed;
        z-index: 9999;
        background: var(--panel-bg);
        color: var(--panel-text);
        border: 2px solid var(--panel-border);
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.22);
        font-family: Arial, Helvetica, sans-serif;
        line-height: 1.38;
      }
      .map-intro-panel {
        top: 16px;
        left: 56px;
        max-width: 380px;
        padding: 14px 16px;
      }
      .map-intro-panel h1 {
        margin: 0 0 8px;
        font-size: 20px;
        line-height: 1.2;
      }
      .map-intro-panel p {
        margin: 7px 0;
      }
      .map-intro-panel .technical-note {
        color: #4c5967;
        font-size: 12px;
      }
      .map-mode-panel {
        top: 16px;
        right: 16px;
        max-width: 310px;
        padding: 12px;
      }
      .map-mode-panel h2,
      .map-legend-panel h2 {
        margin: 0 0 8px;
        font-size: 16px;
      }
      .mode-buttons {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      .mode-buttons button {
        border: 2px solid #4c5967;
        background: #ffffff;
        color: #111111;
        border-radius: 6px;
        padding: 8px;
        font-weight: 700;
        cursor: pointer;
      }
      .mode-buttons button.active {
        background: #111111;
        color: #ffffff;
      }
      .mode-buttons button:focus {
        outline: 3px solid var(--focus-ring);
        outline-offset: 2px;
      }
      .mode-help {
        margin: 8px 0 0;
        font-size: 13px;
        color: #4c5967;
      }
      .scenario-buttons {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-top: 12px;
        padding-top: 10px;
        border-top: 1px solid #c8ccd2;
      }
      .scenario-buttons button {
        border: 2px solid #4c5967;
        background: #ffffff;
        color: #111111;
        border-radius: 6px;
        padding: 8px;
        font-weight: 700;
        cursor: pointer;
      }
      .scenario-buttons button.active {
        background: #24415f;
        color: #ffffff;
      }
      .scenario-buttons button:focus {
        outline: 3px solid var(--focus-ring);
        outline-offset: 2px;
      }
      .map-legend-panel {
        bottom: 24px;
        left: 24px;
        max-width: 310px;
        padding: 12px;
      }
      .legend-row {
        display: grid;
        grid-template-columns: 54px 1fr;
        align-items: center;
        gap: 8px;
        margin: 6px 0;
      }
      .legend-sample {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 46px;
        height: 28px;
        border: 2px solid #111111;
        border-radius: 999px;
        color: #ffffff;
        font-size: 11px;
        font-weight: 800;
      }
      .legend-park {
        background: #88c999;
        color: #111111;
      }
      .legend-route {
        background: repeating-linear-gradient(90deg, #005a32, #005a32 8px, #ffffff 8px, #ffffff 12px);
      }
      .cooling-map-marker {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 46px;
        height: 46px;
        border: 3px solid #111111;
        border-radius: 999px;
        color: #ffffff;
        font-family: Arial, Helvetica, sans-serif;
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 0;
        box-shadow: 0 1px 6px rgba(0, 0, 0, 0.35);
      }
      .cooling-marker { background: var(--cooling); }
      .water-marker { background: var(--water); }
      .washroom-marker { background: var(--washroom); }
      .bench-marker { background: var(--bench); color: #111111; }
      .transit-marker { background: var(--transit); }
      .rapid-marker { background: var(--rapid); }
      .resident-good-marker { background: #6a3d9a; }
      .resident-hot-marker { background: #b2182b; }
      .resident-need-marker { background: #ffff99; color: #111111; }
      .leaflet-control-search {
        margin-top: 88px !important;
        margin-left: 10px !important;
        border: 2px solid var(--panel-border) !important;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.22) !important;
      }
      .leaflet-control-search .search-input {
        font-family: Arial, Helvetica, sans-serif;
        font-size: 15px;
        min-width: 240px;
      }
      .cooling-div-icon {
        background: transparent;
        border: 0;
      }
      body.scenario-senior .transit-marker,
      body.scenario-senior .rapid-marker,
      body.scenario-senior .resident-good-marker,
      body.scenario-senior .resident-hot-marker,
      body.scenario-senior .resident-need-marker {
        opacity: 0.35;
      }
      body.scenario-family .bench-marker,
      body.scenario-family .transit-marker,
      body.scenario-family .rapid-marker,
      body.scenario-family .resident-hot-marker {
        opacity: 0.38;
      }
      body.scenario-heat .bench-marker,
      body.scenario-heat .resident-good-marker,
      body.scenario-heat .resident-need-marker {
        opacity: 0.28;
      }
      body.scenario-resident .cooling-marker,
      body.scenario-resident .water-marker,
      body.scenario-resident .washroom-marker,
      body.scenario-resident .bench-marker,
      body.scenario-resident .transit-marker,
      body.scenario-resident .rapid-marker {
        opacity: 0.28;
      }
      .leaflet-popup-content {
        font-family: Arial, Helvetica, sans-serif;
        font-size: 15px;
        line-height: 1.4;
      }
      .popup-title {
        font-size: 17px;
        font-weight: 800;
        margin-bottom: 6px;
      }
      .popup-source {
        margin-top: 8px;
        color: #4c5967;
        font-size: 12px;
      }
      body.access-senior .map-intro-panel,
      body.access-senior .map-mode-panel,
      body.access-senior .map-legend-panel,
      body.access-senior .leaflet-control-layers,
      body.access-senior .leaflet-popup-content {
        font-size: 19px;
      }
      body.access-senior .map-intro-panel h1 {
        font-size: 25px;
      }
      body.access-senior .cooling-map-marker {
        width: 58px;
        height: 58px;
        font-size: 12px;
      }
      body.access-senior .mode-buttons {
        grid-template-columns: 1fr;
      }
      body.access-senior .scenario-buttons {
        grid-template-columns: 1fr;
      }
      body.access-colorblind .map-intro-panel,
      body.access-colorblind .map-mode-panel,
      body.access-colorblind .map-legend-panel,
      body.access-colorblind .cooling-map-marker {
        border-color: #000000;
      }
      body.access-simple .map-intro-panel {
        max-width: 430px;
      }
      body.access-simple .map-legend-panel {
        font-size: 17px;
      }
      @media (max-width: 760px) {
        .map-intro-panel,
        .map-mode-panel,
        .map-legend-panel {
          left: 10px;
          right: 10px;
          max-width: none;
        }
        .map-intro-panel {
          top: 10px;
        }
        .map-mode-panel {
          top: auto;
          bottom: 175px;
        }
        .map-legend-panel {
          bottom: 10px;
        }
        .mode-buttons {
          grid-template-columns: 1fr 1fr;
        }
        .scenario-buttons {
          grid-template-columns: 1fr 1fr;
        }
        .leaflet-control-search {
          margin-top: 136px !important;
        }
        .leaflet-control-search .search-input {
          min-width: 190px;
        }
      }
    </style>
    """

    panels = """
    <div class="map-intro-panel" role="region" aria-label="Map introduction">
      <h1>Our Neighbourhood Cooling Map</h1>
      <p>Use this map to find nearby shade, water, washrooms, places to sit, civic buildings, and transit.</p>
      <p>Click a marker or park to see what it is. Search by park name if you already know the place you want.</p>
      <p class="technical-note">This is a community resource map. Please check opening hours and conditions before relying on a place during extreme heat.</p>
    </div>

    <div class="map-mode-panel" role="region" aria-label="Map display modes">
      <h2>Display mode</h2>
      <div class="mode-buttons">
        <button type="button" class="active" data-mode="standard">Standard</button>
        <button type="button" data-mode="senior">Large text</button>
        <button type="button" data-mode="colorblind">High contrast</button>
        <button type="button" data-mode="simple">Simple view</button>
      </div>
      <p class="mode-help" id="mode-help">Choose a display mode. Markers use both words and colors so they are easier to read.</p>
      <h2 style="margin-top: 12px;">Scenario</h2>
      <div class="scenario-buttons">
        <button type="button" class="active" data-scenario="all">All</button>
        <button type="button" data-scenario="senior">Senior</button>
        <button type="button" data-scenario="family">Family</button>
        <button type="button" data-scenario="heat">Extreme heat</button>
        <button type="button" data-scenario="resident">Resident input</button>
      </div>
      <p class="mode-help" id="scenario-help">Choose a scenario to highlight the most relevant markers.</p>
    </div>

    <div class="map-legend-panel" role="region" aria-label="Map legend">
      <h2>What to look for</h2>
      <div class="legend-row"><span class="legend-sample cooling-marker">COOL</span><span>Community centres and libraries</span></div>
      <div class="legend-row"><span class="legend-sample water-marker">WATER</span><span>Drinking fountains</span></div>
      <div class="legend-row"><span class="legend-sample washroom-marker">WC</span><span>Public washrooms</span></div>
      <div class="legend-row"><span class="legend-sample bench-marker">SIT</span><span>Benches and places to sit</span></div>
      <div class="legend-row"><span class="legend-sample transit-marker">BUS</span><span>Transit stops</span></div>
      <div class="legend-row"><span class="legend-sample rapid-marker">TRAIN</span><span>SkyTrain or rapid transit station</span></div>
      <div class="legend-row"><span class="legend-sample resident-good-marker">GOOD</span><span>Resident recommended place</span></div>
      <div class="legend-row"><span class="legend-sample resident-hot-marker">HOT</span><span>Resident identified hot spot</span></div>
      <div class="legend-row"><span class="legend-sample resident-need-marker">NEED</span><span>Resident suggestion or need</span></div>
      <div class="legend-row"><span class="legend-sample legend-park">PARK</span><span>Parks and green spaces</span></div>
      <div class="legend-row"><span class="legend-sample legend-route">SHADE</span><span>Possible shady walking route</span></div>
    </div>
    """

    script = """
    <script>
      document.addEventListener("DOMContentLoaded", function () {
        const buttons = document.querySelectorAll(".mode-buttons button");
        const scenarioButtons = document.querySelectorAll(".scenario-buttons button");
        const help = document.getElementById("mode-help");
        const scenarioHelp = document.getElementById("scenario-help");
        const helpText = {
          standard: "Standard mode shows all controls and regular text size.",
          senior: "Large text mode increases labels, popups, and map markers.",
          colorblind: "High contrast mode uses stronger borders, words, and a colorblind-friendly palette.",
          simple: "Simple view hides technical notes and extra layer controls for a calmer public display."
        };
        const scenarioText = {
          all: "All mode shows all mapped resources.",
          senior: "Senior mode highlights places to sit, washrooms, water, and civic cooling places.",
          family: "Family mode highlights water, washrooms, civic buildings, parks, and child-friendly rest options.",
          heat: "Extreme heat mode highlights cooling places, water, washrooms, and transit.",
          resident: "Resident input mode highlights questionnaire and workshop feedback."
        };

        function setMode(mode) {
          document.body.classList.remove("access-senior", "access-colorblind", "access-simple");
          if (mode === "senior") document.body.classList.add("access-senior");
          if (mode === "colorblind") document.body.classList.add("access-colorblind");
          if (mode === "simple") document.body.classList.add("access-simple");
          buttons.forEach((button) => {
            button.classList.toggle("active", button.dataset.mode === mode);
            button.setAttribute("aria-pressed", button.dataset.mode === mode ? "true" : "false");
          });
          if (help) help.textContent = helpText[mode] || helpText.standard;
          localStorage.setItem("coolingMapMode", mode);
        }

        function setScenario(scenario) {
          document.body.classList.remove("scenario-senior", "scenario-family", "scenario-heat", "scenario-resident");
          if (scenario !== "all") document.body.classList.add("scenario-" + scenario);
          scenarioButtons.forEach((button) => {
            button.classList.toggle("active", button.dataset.scenario === scenario);
            button.setAttribute("aria-pressed", button.dataset.scenario === scenario ? "true" : "false");
          });
          if (scenarioHelp) scenarioHelp.textContent = scenarioText[scenario] || scenarioText.all;
          localStorage.setItem("coolingMapScenario", scenario);
        }

        buttons.forEach((button) => {
          button.addEventListener("click", function () {
            setMode(button.dataset.mode);
          });
        });
        scenarioButtons.forEach((button) => {
          button.addEventListener("click", function () {
            setScenario(button.dataset.scenario);
          });
        });
        setMode(localStorage.getItem("coolingMapMode") || "standard");
        setScenario(localStorage.getItem("coolingMapScenario") || "all");
      });
    </script>
    """

    fmap.get_root().header.add_child(folium.Element(css))
    fmap.get_root().header.add_child(folium.Element(f"<title>{PUBLIC_MAP_TITLE}</title>"))
    fmap.get_root().html.add_child(folium.Element(panels))
    fmap.get_root().html.add_child(folium.Element(script))


def make_map(project_area: gpd.GeoDataFrame, buffered_area: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame]) -> Path:
    center = project_area.to_crs(CRS_WGS84).geometry.iloc[0].centroid
    fmap = folium.Map(location=[center.y, center.x], zoom_start=13, tiles="CartoDB positron")

    folium.GeoJson(
        buffered_area,
        name=f"Nearby area, within about {BUFFER_METRES} m",
        style_function=lambda _: {"color": "#777777", "weight": 1, "fillOpacity": 0.03},
    ).add_to(fmap)
    folium.GeoJson(
        project_area,
        name="Main project area",
        style_function=lambda _: {"color": "#333333", "weight": 2, "fillOpacity": 0.04},
    ).add_to(fmap)

    if not layers["parks"].empty:
        parks_layer = folium.GeoJson(
            make_properties_json_safe(layers["parks"]),
            name="Parks and green spaces",
            style_function=lambda _: {"color": "#005a32", "weight": 2, "fillColor": "#88c999", "fillOpacity": 0.52},
            tooltip=folium.GeoJsonTooltip(fields=["name", "category"], aliases=["Name", "Type"]),
            popup=folium.GeoJsonPopup(fields=["name", "category"], aliases=["Park or green space", "Type"]),
        )
        parks_layer.add_to(fmap)
        Search(
            layer=parks_layer,
            search_label="name",
            placeholder="Search park name...",
            collapsed=False,
            position="topleft",
            geom_type="Polygon",
        ).add_to(fmap)

    if not layers["shaded_walking_routes"].empty:
        def route_style(feature):
            category = feature["properties"].get("category")
            color = "#005a32" if category == "Higher shade potential" else "#5aae61"
            weight = 5 if category == "Higher shade potential" else 3
            dash = None if category == "Higher shade potential" else "7, 5"
            return {"color": color, "weight": weight, "opacity": 0.9, "dashArray": dash}

        folium.GeoJson(
            make_properties_json_safe(layers["shaded_walking_routes"]),
            name="Possible shady walking routes",
            style_function=route_style,
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "category", "trees_per_100m"],
                aliases=["Street", "Shade estimate", "Public trees per 100 m"],
            ),
        ).add_to(fmap)

    add_point_layer(fmap, layers["civic_cooling_places"], "Community centres and libraries", "cooling")
    add_point_layer(fmap, layers["drinking_fountains"], "Water fountains", "water")
    add_point_layer(fmap, layers["public_washrooms"], "Public washrooms", "washroom")
    add_point_layer(fmap, layers["benches_osm"], "Benches and places to sit", "bench", cluster=True)
    add_point_layer(fmap, layers["transit_stops_osm"], "Bus and transit stops", "bus", cluster=True)
    add_point_layer(fmap, layers["rapid_transit_stations"], "Train and rapid transit stations", "train")
    add_resident_input_layer(fmap, layers["resident_input"])

    add_public_accessibility_controls(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)

    out = OUTPUT_DIR / "cooling_resources_map.html"
    site_out = SITE_DIR / "index.html"
    fmap.save(out)
    fmap.save(site_out)
    return out


def print_summary(layers: dict[str, gpd.GeoDataFrame]) -> None:
    print("\nLayer summary")
    print("-------------")
    for key, gdf in layers.items():
        print(f"{key}: {len(gdf):,} features")


def main() -> None:
    ensure_dirs()
    project_area, buffered_area = build_project_area()
    export_geojson(project_area, "project_area")
    export_geojson(buffered_area, "project_area_buffer")

    layers = load_city_resources(buffered_area)
    layers["resident_input"] = load_resident_input(buffered_area)
    layers["benches_osm"] = osm_features(
        buffered_area,
        tags={"amenity": "bench"},
        layer="benches_osm",
        category="Bench or place to sit",
    )
    layers["transit_stops_osm"] = osm_features(
        buffered_area,
        tags={
            "highway": "bus_stop",
            "public_transport": ["platform", "station", "stop_position"],
            "railway": "tram_stop",
        },
        layer="transit_stops_osm",
        category="Transit stop",
    )
    layers["shaded_walking_routes"] = build_shaded_routes(buffered_area, layers["public_trees"])

    for key, gdf in layers.items():
        export_geojson(gdf, key)

    # Combined point layer is handy for web tools that prefer one point file.
    combined_points = combine_layers(
        [
            layers["community_centres"],
            layers["libraries"],
            layers["drinking_fountains"],
            layers["public_washrooms"],
            layers["benches_osm"],
            layers["transit_stops_osm"],
            layers["rapid_transit_stations"],
            layers["resident_input"],
        ]
    )
    export_geojson(combined_points, "cooling_resource_points_combined")

    html_path = make_map(project_area, buffered_area, layers)
    print_summary(layers)
    print(f"\nWrote map: {html_path.relative_to(ROOT)}")
    print(f"Wrote GitHub Pages site: {(SITE_DIR / 'index.html').relative_to(ROOT)}")
    print(f"Wrote GeoJSON files to: {GEOJSON_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
