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

from lib.photo_metadata import get_image_metadata

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




def generate_captions(image_dir: str, jsonl_path: str, year_month: str) -> None:
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

                long_filename = f"{year_month}/{filename}"
                new_entry = {
                    "filename": long_filename,
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
    generate_captions(image_dir, jsonl_path, year_month)


if __name__ == "__main__":
    run()
