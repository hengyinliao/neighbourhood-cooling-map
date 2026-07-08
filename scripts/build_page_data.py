"""
Publish cooling-map feature data for the static Leaflet page.

Run scripts/fetch_features.py first when the resource GeoJSON files need to be
created or refreshed. This script reads outputs/geojson and writes one generated
JavaScript data file used by docs/index.html and docs/index.js.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import geopandas as gpd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs"
GEOJSON_DIR = OUTPUT_DIR / "geojson"
SITE_DIR = ROOT / "docs"
MAP_CONFIG_PATH = GEOJSON_DIR / "map_config.json"
MAP_DATA_JS = SITE_DIR / "map_data.js"
PUBLIC_MAP_TITLE = "Our Neighbourhood Cooling Map"
ID_LENGTH = 8

DEFAULT_BUFFER_METRES = 500

CRS_WGS84 = "EPSG:4326"
CRS_LOCAL_METRES = "EPSG:26910"  # NAD83 / UTM zone 10N, suitable for Vancouver.

RESOURCE_LAYERS = [
    "parks",
    "community_centres",
    "libraries",
    "civic_cooling_places",
    "drinking_fountains",
    "public_washrooms",
    # "public_trees", # No need to show all trees since we only need the shade metrics, which are already in the shaded_walking_routes layer
    "rapid_transit_stations",
    "resident_input",
    "benches_osm",
    "transit_stops_osm",
    "shaded_walking_routes",
]


def ensure_dirs() -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)


def empty_feature_collection() -> dict:
    return {"type": "FeatureCollection", "features": []}


def read_geojson(name: str, required: bool = False) -> gpd.GeoDataFrame:
    path = GEOJSON_DIR / f"{name}.geojson"
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing {path.relative_to(ROOT)}. Run scripts/fetch_features.py first.")
        print(f"Skipping missing layer: {path.relative_to(ROOT)}")
        return gpd.GeoDataFrame(columns=["geometry"], crs=CRS_WGS84)

    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_WGS84)
    return gdf.to_crs(CRS_WGS84)


def load_map_config() -> dict:
    if not MAP_CONFIG_PATH.exists():
        return {}
    return json.loads(MAP_CONFIG_PATH.read_text(encoding="utf-8"))


def infer_buffer_metres(buffered_area: gpd.GeoDataFrame, config: dict) -> int:
    if "buffer_metres" in config:
        return int(config["buffer_metres"])
    if not buffered_area.empty and "name" in buffered_area.columns:
        name = str(buffered_area.iloc[0].get("name", ""))
        match = re.search(r"\+\s*(\d+)\s*m", name)
        if match:
            return int(match.group(1))
    return DEFAULT_BUFFER_METRES


def map_center(project_area: gpd.GeoDataFrame) -> list[float]:
    if project_area.empty:
        return [49.2528, -123.1049]
    local = project_area.to_crs(CRS_LOCAL_METRES)
    geometry_union = local.geometry.union_all() if hasattr(local.geometry, "union_all") else local.geometry.unary_union
    centroid = geometry_union.centroid
    point = gpd.GeoSeries([centroid], crs=CRS_LOCAL_METRES).to_crs(CRS_WGS84).iloc[0]
    return [point.y, point.x]


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
    """Convert list-like attribute values so browser-side rendering stays simple."""
    if gdf.empty:
        return gdf
    out = gdf.copy()
    for col in out.columns:
        if col == out.geometry.name:
            continue
        out[col] = out[col].map(json_safe_value)
    return out


def marker_hash_id(longitude: float, latitude: float, name: str) -> str:
    marker_key = f"{longitude},{latitude},{name}"
    return hashlib.sha256(marker_key.encode("utf-8")).digest()[:ID_LENGTH // 2].hex()


def add_marker_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add stable IDs to point features rendered as map markers."""
    if gdf.empty:
        return gdf

    out = gdf.copy()
    ids = []
    for _, row in out.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.geom_type != "Point":
            ids.append(None)
            continue

        name = json_safe_value(row.get("name", "")) or ""
        ids.append(marker_hash_id(geometry.x, geometry.y, str(name)))

    if any(marker_id is not None for marker_id in ids):
        out["id"] = ids
    return out


def gdf_to_feature_collection(gdf: gpd.GeoDataFrame) -> dict:
    if gdf.empty:
        return empty_feature_collection()
    safe = make_properties_json_safe(add_marker_ids(gdf))
    return json.loads(safe.to_json(drop_id=True))


def build_payload(
    project_area: gpd.GeoDataFrame,
    buffered_area: gpd.GeoDataFrame,
    layers: dict[str, gpd.GeoDataFrame],
    config: dict,
) -> dict:
    buffer_metres = infer_buffer_metres(buffered_area, config)
    return {
        "title": PUBLIC_MAP_TITLE,
        "config": {
            **config,
            "buffer_metres": buffer_metres,
            "center": map_center(project_area),
            "zoom": 15,
        },
        "project_area": gdf_to_feature_collection(project_area),
        "project_area_buffer": gdf_to_feature_collection(buffered_area),
        "layers": {name: gdf_to_feature_collection(gdf) for name, gdf in layers.items()},
    }


def write_map_data(payload: dict) -> Path:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    MAP_DATA_JS.write_text(
        "// Generated by scripts/build_page.py. Do not edit by hand.\n"
        f"window.COOLING_MAP_DATA = {serialized};\n",
        encoding="utf-8",
        newline="\n",
    )
    return MAP_DATA_JS


def print_summary(layers: dict[str, gpd.GeoDataFrame]) -> None:
    print("\nLayer summary")
    print("-------------")
    for key, gdf in layers.items():
        print(f"{key}: {len(gdf):,} features")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build docs/map_data.js from outputs/geojson.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    parse_args(argv)
    ensure_dirs()

    project_area = read_geojson("project_area", required=True)
    buffered_area = read_geojson("project_area_buffer", required=True)
    layers = {name: read_geojson(name) for name in RESOURCE_LAYERS}
    payload = build_payload(project_area, buffered_area, layers, load_map_config())
    data_path = write_map_data(payload)

    print_summary(layers)
    print(f"\nWrote map data: {data_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
