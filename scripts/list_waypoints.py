import json
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import os
from pathlib import Path

"""
Extracts waypoint time and name from a gpx file.
For ingestion by LLM.
Current usage: follow up running this script by manually splitting the trip-level
file into country-level files (e.g. southeast-asia_waypoints_02-cambodia.json)
"""
def extract_waypoints_from_gpx(input_file, output_file):
    # Parse the GPX file
    tree = ET.parse(input_file)
    root = tree.getroot()

    # GPX files often use namespaces
    namespaces = {'gpx': 'http://www.topografix.com/GPX/1/1'}

    # Start with a placeholder _general_ waypoint
    waypoints_data = [{"name": "_general_", "time": "", "description": ""}]

    # Find all waypoint (wpt) elements
    for wpt in root.findall('gpx:wpt', namespaces):
        name_elem = wpt.find('gpx:name', namespaces)
        time_elem = wpt.find('gpx:time', namespaces)

        # Extract text if the elements exist
        name = name_elem.text if name_elem is not None else "Unknown"
        time = time_elem.text if time_elem is not None else "Unknown"

        waypoints_data.append({
            "name": name,
            "time": time,
            "description": ""
        })

    # Save to JSON file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(waypoints_data, f, indent=4)

    print(f"Successfully generated {output_file} with {len(waypoints_data)} waypoints.")


if __name__ == "__main__":
    load_dotenv()
    # NOTE: USING RAW UN-OBFUSCATED GPX FILES FOR NOW
    DEFAULT_GPX_DIR = os.path.join(os.getenv("PRIVATE_DATA_DIR"),"findpenguins")
    DEFAULT_OUTPUT_DIR = os.path.join(os.getenv("INTERIM_DATA_DIR"), "findpenguins")

    inputs = list(Path(DEFAULT_GPX_DIR).glob("*.gpx"))


    for infile in inputs:
        filename = f"{infile.stem}_waypoints.json"
        outfile = os.path.join(DEFAULT_OUTPUT_DIR, filename)

        extract_waypoints_from_gpx(infile, outfile)
