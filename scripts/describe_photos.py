import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional

import click
from dotenv import load_dotenv
from ollama import chat
from PIL import Image

# PROMPT = "Write a short caption for this photo."
# PROMPT = "Describe this image in a one-sentence caption."
PROMPT = """Describe this image using exactly two lines:
1. A description of the main subject.
2. A description of the background details and setting."""
MODEL = "ahmadwaqar/smolvlm2-2.2b-instruct:latest"


# Only necessary in certain prompts
def clean_llm_caption(raw_text):
    # 1. Split the text into individual lines
    lines = raw_text.strip().split("\n")

    cleaned_sentences = []
    for line in lines:
        # 2. Use regex to remove leading numbers, dots, dashes, or spaces
        # Pattern: ^[\s\d\.\)\-]+ matches start of line, digits, dots, parens, dashes
        clean_line = re.sub(r"^\s*[\d\.\)\-]+\s*", "", line)

        if clean_line:  # Avoid adding empty lines
            cleaned_sentences.append(clean_line.strip())

    # 3. Join them back together with a single space
    return " ".join(cleaned_sentences)


def has_not_screened_marker(directory: str) -> bool:
    """Return True if a file named 'NOT_SCREENED' (no extension) exists in the given directory."""
    marker_path = os.path.join(directory, "NOT_SCREENED")
    return os.path.isfile(marker_path)


def get_image_metadata(file_path: str) -> Dict[str, Any]:
    """Extracts camera model, timestamp, and rich GPS coordinates from an image."""
    metadata: Dict[str, Any] = {
        "model": None,
        "timestamp": None,
        "location": None,
        "orientation": None,
    }

    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            if not exif:
                return metadata

            metadata["model"] = exif.get(272)
            metadata["timestamp"] = exif.get(36867) or exif.get(306)
            metadata["orientation"] = exif.get(274, 1)

            if hasattr(exif, "get_ifd"):
                gps_ifd = exif.get_ifd(34853)
                if gps_ifd:
                    # Helper to safely convert EXIF fractions (rationals) to floats
                    def parse_rational(val: Any) -> Optional[float]:
                        if val is None:
                            return None
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            if isinstance(val, tuple) and len(val) == 2:
                                return (
                                    float(val[0]) / float(val[1])
                                    if val[1] != 0
                                    else 0.0
                                )
                            return None

                    # 1. Base Coordinates (Lat/Lon)
                    if 2 in gps_ifd and 4 in gps_ifd:
                        lat_tuple = gps_ifd[2]
                        lon_tuple = gps_ifd[4]

                        lat = (
                            parse_rational(lat_tuple[0])
                            + (parse_rational(lat_tuple[1]) / 60.0)
                            + (parse_rational(lat_tuple[2]) / 3600.0)
                        )
                        if gps_ifd.get(1) == "S":
                            lat = -lat

                        lon = (
                            parse_rational(lon_tuple[0])
                            + (parse_rational(lon_tuple[1]) / 60.0)
                            + (parse_rational(lon_tuple[2]) / 3600.0)
                        )
                        if gps_ifd.get(3) == "W":
                            lon = -lon

                        metadata["location"] = {
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                        }

                    # If we have base coordinates, grab the rest of the spatial data
                    if metadata["location"]:
                        # 2. Altitude (Tags 5 & 6)
                        altitude = parse_rational(gps_ifd.get(6))
                        if altitude is not None:
                            alt_ref = gps_ifd.get(5, 0)
                            # alt_ref 1 means below sea level
                            if alt_ref in [1, b"\x01", "1"]:
                                altitude = -altitude
                            metadata["location"]["altitude"] = round(altitude, 2)

                        # 3. Image Direction / Heading (Tags 16 & 17)
                        direction = parse_rational(gps_ifd.get(17))
                        if direction is not None:
                            metadata["location"]["heading"] = round(direction, 2)
                            # Usually 'T' for True North or 'M' for Magnetic North
                            ref = gps_ifd.get(16)
                            if isinstance(ref, bytes):
                                ref = ref.decode("utf-8", "ignore")
                            metadata["location"]["heading_ref"] = ref

                        # 4. Positioning Error / Accuracy in meters (Tag 31)
                        error = parse_rational(gps_ifd.get(31))
                        if error is not None:
                            metadata["location"]["accuracy_meters"] = round(error, 2)

                        # 5. GPS Timestamp (Tags 7 & 29)
                        gps_date = gps_ifd.get(29)
                        gps_time = gps_ifd.get(7)
                        if gps_date and gps_time:
                            try:
                                h = int(parse_rational(gps_time[0]))
                                m = int(parse_rational(gps_time[1]))
                                s = int(parse_rational(gps_time[2]))
                                metadata["location"]["gps_timestamp"] = (
                                    f"{gps_date} {h:02d}:{m:02d}:{s:02d}"
                                )
                            except Exception:
                                pass

    except Exception as e:
        print(
            f"Warning: Could not read EXIF data for {os.path.basename(file_path)} - {e}"
        )

    return metadata


def generate_captions(image_dir: str, jsonl_path: str) -> None:
    # Load existing progress if the file already exists
    processed_files: set[str] = set()

    # 1. Load existing progress by reading line-by-line
    if os.path.exists(jsonl_path):
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        if "filename" in item:
                            processed_files.add(item["filename"])
            print(
                f"Loaded existing progress. Found {len(processed_files)} processed items."
            )
        except json.JSONDecodeError:
            print("Warning: Existing JSONL file contains corrupted lines.")

    # 3. Gather and sort images alphabetically
    valid_extensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    all_files = os.listdir(image_dir)
    image_files = [f for f in all_files if f.lower().endswith(valid_extensions)]
    image_files.sort()
    image_file_count = len(image_files)

    with open(jsonl_path, "a", encoding="utf-8") as f:
        for i, filename in enumerate(image_files):
            start_time = time.time()
            if filename in processed_files:
                print(f"Skipping {filename} (already captioned).")
                continue

            # --- Metadata Extraction & Filtering ---
            metadata = get_image_metadata(os.path.join(image_dir, filename))

            file_path = os.path.join(image_dir, filename)

            try:
                # Retry a couple of times if the model starts rambling.
                MAX_RETRIES = 3
                attempts = 0

                while attempts < MAX_RETRIES:
                    # On retries, we add a "Be brief" hint to the prompt
                    current_prompt = (
                        PROMPT
                        if attempts == 0
                        else f"{PROMPT} (IMPORTANT: Be very concise!)"
                    )

                    response = chat(
                        model=MODEL,
                        messages=[
                            {
                                "role": "user",
                                "content": current_prompt,
                                "images": [file_path],
                            }
                        ],
                        options={
                            "num_thread": 4,
                            "num_ctx": 1024,
                            # Increase temp on retries (0.1, 0.4, 0.7) to encourage a different result
                            "temperature": 0.1 + (attempts * 0.3),
                            "num_predict": 120,
                        },
                    )

                    # Check why the model stopped
                    if response.get("done_reason") == "length":
                        print(f"Attempt {attempts + 1} cut off.")
                        attempts += 1
                    else:
                        break

                caption = clean_llm_caption(response.message.content)

                new_entry = {
                    "filename": filename,
                    "caption": caption,
                    "timestamp": metadata["timestamp"],
                    "location": metadata["location"],
                }

                # Write a single JSON string followed by a newline directly to the file
                f.write(json.dumps(new_entry) + "\n")
                f.flush()  # Force write to disk immediately for crash resilience
                os.fsync(f.fileno())

                processed_files.add(filename)

            except Exception as e:
                print(f"\nError processing {filename}: {e}")
                print(
                    "Script paused or crashed. You can safely run it again to resume."
                )
                break

            end_time = time.time()
            elapsed_time = end_time - start_time
            remaining_photos = image_file_count - i - 1
            print(
                f"Processed {filename} in {elapsed_time:.2f} seconds. {remaining_photos} remaining."
            )

    print(f"\nDone! Captions saved to {jsonl_path}")


@click.command()
@click.argument("year_month", type=str)
def run(year_month: str) -> None:
    """
    Process a directory of images to generate AI captions and extract EXIF/PostGIS data.

    YEAR_MONTH: Subdirectory of PRIVATE_DATA_DIR/photos, e.g. '2024/07'
    """
    # TODO: this script might be doing too much - AI captions plus exif data
    load_dotenv()

    image_dir = os.path.join(os.getenv("PRIVATE_DATA_DIR"), "photos", year_month)
    if not os.path.isdir(image_dir):
        raise SystemExit(
            f"Error: The directory '{image_dir}' does not exist or is not a valid folder."
        )

    if has_not_screened_marker(image_dir):
        raise SystemExit(
            f"Error: The directory '{image_dir}' has a 'NOT_SCREENED' marker file. Skipping..."
        )

    output_dir = os.path.join(os.getenv("INTERIM_DATA_DIR"), "photos")
    os.makedirs(output_dir, exist_ok=True)
    current_date = datetime.now().strftime("%Y-%m-%d")
    output_filename = f"captions_{year_month.replace('/', '-')}_{current_date}.jsonl"
    jsonl_path = os.path.join(output_dir, output_filename)
    generate_captions(image_dir, jsonl_path)


if __name__ == "__main__":
    run()
