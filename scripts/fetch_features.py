"""
Download and prepare resource GeoJSON layers for the cooling map.

This step:
1. Defines the project area from City of Vancouver local area boundaries.
2. Downloads and caches public open-data layers.
3. Cleans common fields into a simple schema.
4. Clips resources to the buffered project area.
5. Builds a rough shaded-walking-route proxy from public trees near OSM walking
   network segments.
6. Exports GeoJSON layers to outputs/geojson.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import pandas as pd
import requests

try:
    import osmnx as ox
except ImportError:  # pragma: no cover - handled gracefully for users
    ox = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "outputs"
GEOJSON_DIR = OUTPUT_DIR / "geojson"
CONFIG_DIR = ROOT / "config"
LAYER_PROPERTIES_PATH = CONFIG_DIR / "layer_properties.toml"
RESIDENT_INPUT_CSV = DATA_DIR / "resident_input_template.csv"
MAP_CONFIG_PATH = GEOJSON_DIR / "map_config.json"

VANCOUVER_OPEN_DATA_EXPORT = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "{dataset}/exports/geojson?lang=en&timezone=America%2FVancouver"
)

DEFAULT_LOCAL_AREAS = ["Mount Pleasant", "Riley Park", "South Cambie"]
DEFAULT_BUFFER_METRES = 500

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

TEMPLATE_PATTERN = re.compile(r"{{(.*?)}}", re.DOTALL)
SAFE_TEMPLATE_GLOBALS = {
    "__builtins__": {},
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "str": str,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_DIR.mkdir(parents=True, exist_ok=True)


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


def read_city_layer(key: str, refresh: bool = False) -> gpd.GeoDataFrame:
    path = download_city_geojson(CITY_DATASETS[key], refresh=refresh)
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


def build_project_area(
    local_areas: list[str],
    buffer_metres: int,
    refresh: bool = False,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    local_area_boundaries = read_city_layer("local_area_boundary", refresh=refresh)
    selected = local_area_boundaries[local_area_boundaries["name"].isin(local_areas)].copy()
    missing = sorted(set(local_areas) - set(selected["name"]))
    if missing:
        raise ValueError(f"Missing local areas in source data: {missing}")

    area = selected.to_crs(CRS_LOCAL_METRES).dissolve()
    area["name"] = " / ".join(local_areas)
    area["layer"] = "project_area"
    area = area[["name", "layer", "geometry"]].to_crs(CRS_WGS84)

    buffered = area.to_crs(CRS_LOCAL_METRES).copy()
    buffered["geometry"] = buffered.geometry.buffer(buffer_metres)
    buffered["name"] = f"{area.iloc[0]['name']} + {buffer_metres} m"
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


def missing_to_none(value):
    if isinstance(value, (list, tuple, set, dict)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


class PropertyContext(dict):
    """Allow template expressions to use either name or properties.name."""

    def __missing__(self, key):
        return None

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None


def load_layer_properties_config(path: Path = LAYER_PROPERTIES_PATH) -> dict[str, dict[str, Any]]:
    """Load per-layer output property mappings from TOML."""
    if not path.exists():
        return {}

    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - used by Python < 3.11
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            return parse_simple_layer_properties_config(path)

    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    layers = config.get("layers", {})
    if not isinstance(layers, dict):
        raise ValueError(f"{path} must contain a [layers] table.")

    return {str(layer): dict(mapping) for layer, mapping in layers.items()}


def parse_simple_layer_properties_config(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the simple [layers.<name>] TOML shape without an external dependency."""
    layers: dict[str, dict[str, Any]] = {}
    current_layer: str | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            current_layer = section.removeprefix("layers.") if section.startswith("layers.") else None
            if current_layer:
                layers.setdefault(current_layer, {})
            continue
        if current_layer is None:
            continue
        if "=" not in line:
            raise ValueError(f"Invalid TOML assignment in {path} at line {line_number}: {raw_line!r}")

        key, value = (part.strip() for part in line.split("=", 1))
        try:
            layers[current_layer][key] = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            lower_value = value.lower()
            if lower_value == "true":
                layers[current_layer][key] = True
            elif lower_value == "false":
                layers[current_layer][key] = False
            else:
                raise ValueError(f"Unsupported TOML value in {path} at line {line_number}: {raw_line!r}")
    return layers


def row_properties(row: pd.Series, geometry_name: str) -> PropertyContext:
    properties = {
        key: missing_to_none(value)
        for key, value in row.items()
        if key != geometry_name
    }
    context = PropertyContext(properties)
    context["properties"] = context
    return context


def evaluate_template_expression(expression: str, context: PropertyContext):
    # TOML basic strings decode \n before Python sees the embedded expression.
    expression = expression.replace("\n", "\\n")
    return eval(expression, SAFE_TEMPLATE_GLOBALS, context)


def render_property_template(template, context: PropertyContext):
    if not isinstance(template, str):
        return template

    full_expression = TEMPLATE_PATTERN.fullmatch(template.strip())
    if full_expression:
        return json_safe_value(evaluate_template_expression(full_expression.group(1).strip(), context))

    def replace_expression(match: re.Match) -> str:
        value = evaluate_template_expression(match.group(1).strip(), context)
        if value is None:
            return ""
        return str(json_safe_value(value))

    return TEMPLATE_PATTERN.sub(replace_expression, template)


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
    property_map: Mapping[str, Any],
) -> gpd.GeoDataFrame:
    """Render configured output properties for a layer."""
    if not property_map:
        raise ValueError(f"Missing layer property mapping for {layer}.")

    gdf = gdf.copy()
    out = gpd.GeoDataFrame(index=gdf.index, geometry=gdf.geometry, crs=gdf.crs)

    for out_col, template in property_map.items():
        values = []
        for idx, row in gdf.iterrows():
            context = row_properties(row, gdf.geometry.name)
            try:
                values.append(render_property_template(template, context))
            except Exception as exc:
                raise ValueError(
                    f"Failed to evaluate layer_properties template for "
                    f"{layer}.{out_col} at source row {idx}: {template!r}"
                    f" at {context!r}"
                ) from exc
        out[out_col] = pd.Series(values, index=gdf.index, dtype="object")
    return out


def empty_resource_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=["name", "category", "layer", "source", "geometry"], crs=CRS_WGS84)


def combine_layers(layers: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    layers = [layer for layer in layers if not layer.empty]
    if not layers:
        return empty_resource_gdf()
    return gpd.GeoDataFrame(
        pd.concat(layers, ignore_index=True),
        crs=layers[0].crs,
    ).to_crs(CRS_WGS84)


def load_city_resources(
    buffered_area: gpd.GeoDataFrame,
    refresh: bool = False,
    layer_properties: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, gpd.GeoDataFrame]:
    layer_properties = layer_properties or {}
    layers: dict[str, gpd.GeoDataFrame] = {}

    parks = clip_to_area(read_city_layer("parks", refresh=refresh), buffered_area)
    layers["parks"] = standardize(
        parks,
        layer="parks",
        property_map=layer_properties.get("parks", {}),
    )

    community_centres = clip_to_area(read_city_layer("community_centres", refresh=refresh), buffered_area)
    layers["community_centres"] = standardize(
        community_centres,
        layer="community_centres",
        property_map=layer_properties.get("community_centres", {}),
    )

    libraries = clip_to_area(read_city_layer("libraries", refresh=refresh), buffered_area)
    layers["libraries"] = standardize(
        libraries,
        layer="libraries",
        property_map=layer_properties.get("libraries", {}),
    )

    layers["civic_cooling_places"] = combine_layers(
        [
            layers["community_centres"],
            layers["libraries"]
        ]
    )

    fountains = clip_to_area(read_city_layer("drinking_fountains", refresh=refresh), buffered_area)
    layers["drinking_fountains"] = standardize(
        fountains,
        layer="drinking_fountains",
        property_map=layer_properties.get("drinking_fountains", {}),
    )

    washrooms = clip_to_area(read_city_layer("public_washrooms", refresh=refresh), buffered_area)
    layers["public_washrooms"] = standardize(
        washrooms,
        layer="public_washrooms",
        property_map=layer_properties.get("public_washrooms", {}),
    )

    trees = clip_to_area(read_city_layer("public_trees", refresh=refresh), buffered_area)
    layers["public_trees"] = standardize(
        trees,
        layer="public_trees",
        property_map=layer_properties.get("public_trees", {}),
    )

    rapid = clip_to_area(read_city_layer("rapid_transit_stations", refresh=refresh), buffered_area)
    layers["rapid_transit_stations"] = standardize(
        rapid,
        layer="rapid_transit_stations",
        property_map=layer_properties.get("rapid_transit_stations", {}),
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


def region_slug(local_areas: list[str], buffer_metres: int) -> str:
    raw = "-".join(local_areas + [f"{buffer_metres}m"]).lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def osm_features(
    boundary: gpd.GeoDataFrame,
    tags: dict,
    layer: str,
    cache_slug: str,
    refresh: bool = False,
    layer_properties: Mapping[str, Mapping[str, Any]] | None = None,
) -> gpd.GeoDataFrame:
    property_map = (layer_properties or {}).get(layer, {})
    cache_path = RAW_DIR / f"{layer}_{cache_slug}.geojson"
    if cache_path.exists() and not refresh:
        cached = gpd.read_file(cache_path).to_crs(CRS_WGS84)
        return standardize(cached, layer=layer, property_map=property_map)

    if ox is None:
        print(f"Skipping {layer}: osmnx is not installed.")
        return empty_resource_gdf()

    polygon = boundary.to_crs(CRS_WGS84).geometry.iloc[0]
    print(f"Downloading OSM {layer}...")
    try:
        gdf = ox.features_from_polygon(polygon, tags=tags)
    except Exception as exc:
        print(f"Skipping {layer}: OSM request failed ({exc}).")
        return empty_resource_gdf()

    if gdf.empty:
        return empty_resource_gdf()

    gdf = gdf.reset_index()
    # Convert polygons/lines for amenities to representative points for a simple map.
    point_geom = gdf.to_crs(CRS_LOCAL_METRES).geometry.representative_point().to_crs(CRS_WGS84)
    gdf = gdf.set_geometry(point_geom).set_crs(CRS_WGS84, allow_override=True)
    out = standardize(
        gdf,
        layer=layer,
        property_map=property_map,
    )
    out.to_file(cache_path, driver="GeoJSON")
    return out


def build_shaded_routes(
    boundary: gpd.GeoDataFrame,
    trees: gpd.GeoDataFrame,
    cache_slug: str,
    refresh: bool = False,
) -> gpd.GeoDataFrame:
    """Approximate shaded walking routes by counting public trees near street edges."""
    cache_path = RAW_DIR / f"shaded_walking_routes_{cache_slug}.geojson"
    if cache_path.exists() and not refresh:
        return gpd.read_file(cache_path).to_crs(CRS_WGS84)

    if ox is None:
        print("Skipping shaded routes: osmnx is not installed.")
        return empty_resource_gdf()
    if trees.empty:
        print("Skipping shaded routes: no public trees found in project buffer.")
        return empty_resource_gdf()

    polygon = boundary.to_crs(CRS_WGS84).geometry.iloc[0]
    print("Downloading OSM walking network for shaded-route proxy...")
    try:
        graph = ox.graph_from_polygon(polygon, network_type="walk", simplify=True)
        _, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    except Exception as exc:
        print(f"Skipping shaded routes: OSM network request failed ({exc}).")
        return empty_resource_gdf()

    if edges.empty:
        return empty_resource_gdf()

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
        return empty_resource_gdf()

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


def write_map_config(local_areas: list[str], buffer_metres: int) -> Path:
    payload = {
        "local_areas": local_areas,
        "buffer_metres": buffer_metres,
        "geojson_dir": str(GEOJSON_DIR.relative_to(ROOT)),
    }
    MAP_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return MAP_CONFIG_PATH


def print_summary(layers: dict[str, gpd.GeoDataFrame]) -> None:
    print("\nLayer summary")
    print("-------------")
    for key, gdf in layers.items():
        print(f"{key}: {len(gdf):,} features")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch cooling-map resource layers into outputs/geojson.")
    parser.add_argument(
        "--local-area",
        action="append",
        dest="local_areas",
        help="City of Vancouver local area name. Repeat for multiple areas.",
    )
    parser.add_argument(
        "--buffer-metres",
        type=int,
        default=DEFAULT_BUFFER_METRES,
        help=f"Buffer around the selected local areas in metres. Default: {DEFAULT_BUFFER_METRES}.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download cached City and OSM source data.",
    )
    parser.add_argument(
        "--layer-properties",
        type=Path,
        default=LAYER_PROPERTIES_PATH,
        help=f"Layer property mapping TOML. Default: {LAYER_PROPERTIES_PATH.relative_to(ROOT)}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    local_areas = args.local_areas or DEFAULT_LOCAL_AREAS
    buffer_metres = args.buffer_metres
    cache_slug = region_slug(local_areas, buffer_metres)
    layer_properties = load_layer_properties_config(args.layer_properties)

    ensure_dirs()
    project_area, buffered_area = build_project_area(local_areas, buffer_metres, refresh=args.refresh)
    export_geojson(project_area, "project_area")
    export_geojson(buffered_area, "project_area_buffer")

    layers = load_city_resources(buffered_area, refresh=args.refresh, layer_properties=layer_properties)
    layers["resident_input"] = load_resident_input(buffered_area)
    layers["benches_osm"] = osm_features(
        buffered_area,
        tags={"amenity": "bench"},
        layer="benches_osm",
        cache_slug=cache_slug,
        refresh=args.refresh,
        layer_properties=layer_properties,
    )
    layers["transit_stops_osm"] = osm_features(
        buffered_area,
        tags={
            "highway": "bus_stop",
            "public_transport": ["platform", "station", "stop_position"],
            "railway": "tram_stop",
        },
        layer="transit_stops_osm",
        cache_slug=cache_slug,
        refresh=args.refresh,
        layer_properties=layer_properties,
    )
    layers["shaded_walking_routes"] = build_shaded_routes(
        buffered_area,
        layers["public_trees"],
        cache_slug=cache_slug,
        refresh=args.refresh,
    )

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
    config_path = write_map_config(local_areas, buffer_metres)

    print_summary(layers)
    print(f"\nWrote GeoJSON files to: {GEOJSON_DIR.relative_to(ROOT)}")
    print(f"Wrote map config: {config_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
