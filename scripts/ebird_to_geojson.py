import csv
import json
import os
import shutil
from collections import defaultdict
from typing import Any

import click
from dotenv import load_dotenv
from lib.gps_utils import (compute_obfuscated_location, haversine_distance,
                           load_sensitive_zones)

# Trip date range filter (inclusive)
DATE_MIN = "2024-07-20"
DATE_MAX = "2025-12-01"


def build_sensitive_zones(sensitive_zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build sensitive zone dicts from config entries."""
    zones = []
    for config in sensitive_zones:
        zones.append({"lat": config["lat"], "lon": config["lon"], "radius": config["radius"],
                      "displacement": config["displacement"], "bearing": config["bearing"]})
        print(f"  [Sensitive zone] '{config['key']}': radius={config['radius']}km, displacement=({config['displacement']:.2f}km @ {config['bearing']:.1f}°)")
    return zones


def apply_obfuscation(
    lat: float, lon: float, zones: list[dict[str, Any]]
) -> tuple[float, float]:
    """If (lat, lon) falls within any sensitive zone's radius, displace it by that zone's vector."""
    for zone in zones:
        if haversine_distance(lat, lon, zone["lat"], zone["lon"]) <= zone["radius"]:
            return compute_obfuscated_location(zone, lat, lon)
    return lat, lon


def convert_ebird_csv_to_geojson(
    input_csv: str,
    output_geojson: str,
    sensitive_zones: list[dict[str, Any]] | None = None,
) -> bool:
    """
    Converts an eBird MyEBirdData CSV export into a GeoJSON FeatureCollection.

    One feature per hotspot (Location ID). Species are aggregated across all
    checklists at the same hotspot. Warns if multiple checklists share a hotspot,
    since they will overlap perfectly on the map.

    Args:
        input_csv: Path to the input CSV file.
        output_geojson: Path where the output GeoJSON file will be saved.
        sensitive_zones: Optional list of obfuscation zones.
    """
    print(f"Reading {input_csv}...")

    # hotspot_id -> aggregated data
    # checklists: checklist_id -> {date, species}
    hotspots: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "name": "",
        "lat": 0.0,
        "lon": 0.0,
        "checklists": {},
    })

    with open(input_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row["Latitude"])
                lon = float(row["Longitude"])
            except (ValueError, KeyError):
                continue

            if lat == 0 and lon == 0:
                continue

            location_id = row.get("Location ID", "").strip()
            if not location_id:
                continue

            hs = hotspots[location_id]
            hs["name"] = row.get("Location", "").strip()
            hs["lat"] = lat
            hs["lon"] = lon
            date = row.get("Date", "").strip()
            if not (DATE_MIN <= date <= DATE_MAX):
                continue

            # Merlin checklists are intentionally excluded. Merlin passively accumulates
            # species in the background without deliberate counting effort, making its
            # species counts, individual counts, and durations unreliable. It also tends
            # to duplicate species already recorded in intentional checklists nearby.
            # Protocol string in eBird exports: "eBird - Merlin Bird ID"
            if row.get("Protocol", "").strip() == "eBird - Merlin Bird ID":
                continue

            checklist_id = row.get("Submission ID", "").strip()
            if checklist_id not in hs["checklists"]:
                hs["checklists"][checklist_id] = {
                    "date": date,
                    "start_time": row.get("Time", "").strip(),
                    "duration_min": row.get("Duration (Min)", "").strip() or None,
                    "individual_count": 0,
                    "species": {},  # common_name -> scientific_name
                }
            cl = hs["checklists"][checklist_id]
            common = row.get("Common Name", "").strip()
            scientific = row.get("Scientific Name", "").strip()
            cl["species"][common] = scientific
            try:
                cl["individual_count"] += int(row.get("Count", 0))
            except (ValueError, TypeError):
                pass  # "X" or blank — skip

    print(f"Filtering to date range {DATE_MIN} – {DATE_MAX}...")
    hotspots = {lid: hs for lid, hs in hotspots.items() if hs["checklists"]}

    features = []
    for location_id, hs in hotspots.items():
        lat, lon = hs["lat"], hs["lon"]
        if sensitive_zones:
            lat, lon = apply_obfuscation(lat, lon, sensitive_zones)

        checklists = sorted(
            [
                {
                    "id": cid,
                    "url": f"https://ebird.org/checklist/{cid}",
                    "date": cl["date"],
                    "start_time": cl["start_time"],
                    "duration_min": int(cl["duration_min"]) if cl["duration_min"] else None,
                    "individual_count": cl["individual_count"],
                    "species_count": len(cl["species"]),
                }
                for cid, cl in hs["checklists"].items()
            ],
            key=lambda c: c["date"],
        )
        all_species_map: dict[str, str] = {}
        for cl in hs["checklists"].values():
            all_species_map.update(cl["species"])
        species_list = sorted(
            [{"common_name": cn, "scientific_name": sn} for cn, sn in all_species_map.items()],
            key=lambda s: s["common_name"],
        )
        dates = [c["date"] for c in checklists]
        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "title": hs["name"],
                "location_id": location_id,
                "hotspot_url": f"https://ebird.org/hotspot/{location_id}",
                "checklists": checklists,
                "species": species_list,
                "species_count": len(species_list),
                "min_date": dates[0],
                "max_date": dates[-1],
                # TODO: lifers count (requires personal life list data)
                # TODO: rarity species count (requires eBird rarity threshold data)
                # TODO: global checklist count at this hotspot (requires eBird API)
            },
        }
        features.append(feature)

    geojson_data = {"type": "FeatureCollection", "features": features}

    with open(output_geojson, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, indent=2)

    print(f"Successfully wrote {len(features)} hotspot(s) to {output_geojson}")
    return True


@click.command()
@click.argument("input_csv", type=click.Path(exists=True, path_type=str))
@click.option(
    "--deploy-path",
    type=click.Path(path_type=str),
    default=None,
    help='Folder to copy the output GeoJSON to for deployment. Default: FINAL_DATA_DIR/../DEPLOY_TARGET/observations. Pass "" to disable.',
)
def run(input_csv: str, deploy_path: str | None) -> None:
    """
    Convert eBird MyEBirdData CSV export to a GeoJSON FeatureCollection for map view.

    Produces one feature per hotspot with species count, hotspot link, and checklist link(s).
    Obfuscates hotspot locations near sensitive areas using sensitive_locations.json.
    Output is written to FINAL_DATA_DIR/ebird.geojson.

    INPUT_CSV: Path to the MyEBirdData.csv export file.
    """
    load_dotenv()

    output_file = os.path.join(os.getenv("FINAL_DATA_DIR"), "ebird.geojson")
    default_deploy_path = os.path.join(os.getenv("DEPLOY_TARGET"), "observations")

    if deploy_path is None:
        deploy_path = default_deploy_path
    elif deploy_path == "":
        deploy_path = None

    if deploy_path and not os.path.exists(deploy_path):
        raise SystemExit(f"[ERROR] Deploy path not found: {deploy_path}")

    print("Building sensitive zones for obfuscation...")
    sensitive_zones = build_sensitive_zones(load_sensitive_zones())

    success = convert_ebird_csv_to_geojson(input_csv, output_file, sensitive_zones)

    if success and deploy_path:
        try:
            shutil.copy(output_file, deploy_path)
            print(f"  [SUCCESS] Copied {output_file} -> {deploy_path}")
        except Exception as e:
            raise SystemExit(f"  [ERROR] Copy failed: {e}")


if __name__ == "__main__":
    run()
