import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import click
from dotenv import load_dotenv


"""
Extracts waypoint time and name from a gpx file.

Current usage: follow up running this script by manually splitting the trip-level
file into country-level files (e.g. southeast-asia_waypoints_02-cambodia.json),
keeping the _general_ waypoint in each one
"""


def extract_waypoints_from_gpx(input_file: str | Path, output_file: str | Path) -> None:
    # Parse the GPX file
    tree = ET.parse(input_file)
    root = tree.getroot()

    # GPX files often use namespaces
    namespaces = {"gpx": "http://www.topografix.com/GPX/1/1"}

    # Start with a placeholder _general_ waypoint
    waypoints_data = [{"name": "_general_", "time": "", "description": ""}]

    # Find all waypoint (wpt) elements
    for wpt in root.findall("gpx:wpt", namespaces):
        name_elem = wpt.find("gpx:name", namespaces)
        time_elem = wpt.find("gpx:time", namespaces)

        # Extract text if the elements exist
        name = name_elem.text if name_elem is not None else "Unknown"
        time = time_elem.text if time_elem is not None else "Unknown"

        waypoints_data.append({"name": name, "time": time, "description": ""})

    new_content = json.dumps(waypoints_data, indent=4)

    output_path = Path(output_file)
    if output_path.exists():
        existing_content = output_path.read_text(encoding="utf-8")
        if existing_content == new_content:
            print(f"  [NO CHANGE] {output_file} ({len(waypoints_data)} waypoints)")
        else:
            print(f"  [CHANGED]   {output_file} ({len(waypoints_data)} waypoints)")
    else:
        print(f"  [NEW]       {output_file} ({len(waypoints_data)} waypoints)")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(new_content)


@click.command()
@click.option(
    "--gpx-dir",
    type=click.Path(exists=True, file_okay=False, path_type=str),
    default=None,
    help="Directory containing .gpx files. Default: $PRIVATE_DATA_DIR/findpenguins",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=str),
    default=None,
    help="Directory to write output JSON files. Default: $INTERIM_DATA_DIR/findpenguins",
)
def run(gpx_dir: str | None, output_dir: str | None) -> None:
    """
    Extract waypoint names and times from GPX files and write them as JSON.

    Produces one _waypoints.json file per .gpx input. Logs whether each output
    file is new, changed, or unchanged compared to the existing file on disk.

    NOTE: Uses raw un-obfuscated GPX files from PRIVATE_DATA_DIR.
    """
    load_dotenv()

    if gpx_dir is None:
        gpx_dir = os.path.join(os.getenv("PRIVATE_DATA_DIR"), "findpenguins")
    if output_dir is None:
        output_dir = os.path.join(os.getenv("INTERIM_DATA_DIR"), "findpenguins")

    inputs = sorted(Path(gpx_dir).glob("*.gpx"))
    if not inputs:
        print(f"No .gpx files found in {gpx_dir}")
        return

    print(f"Processing {len(inputs)} GPX file(s) from {gpx_dir}...")
    for infile in inputs:
        filename = f"{infile.stem}_waypoints.json"
        outfile = os.path.join(output_dir, filename)
        extract_waypoints_from_gpx(infile, outfile)


if __name__ == "__main__":
    run()
