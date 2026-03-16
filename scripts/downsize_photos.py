import os
from pathlib import Path

import click
import imagehash
from PIL import Image


def process_photos(
    input_folder: str,
    output_folder: str,
    max_size: tuple[int, int] = (1920, 1920),
    hash_cutoff: int = 18,
) -> None:
    """
    Downsizes images and skips adjacent duplicates, keeping the last in a series.
    Copies EXIF data to the new images.
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    valid_exts = ('.jpg', '.jpeg')
    files = [f for f in os.listdir(input_folder) if f.lower().endswith(valid_exts)]
    files.sort()

    if not files:
        print(f"No JPG images found in the input folder: {input_folder}")
        return

    current_group = []
    prev_hash = None

    def save_last_of_group(group: list[str]) -> None:
        """Processes and saves the very last image of the duplicate group."""
        if not group:
            return

        last_file = group[-1]
        input_path = os.path.join(input_folder, last_file)
        output_path = os.path.join(output_folder, last_file)

        try:
            with Image.open(input_path) as img:
                # 1. Extract the EXIF data
                exif_data = img.info.get('exif')

                # 2. Downsize the image
                img.thumbnail(max_size, Image.Resampling.LANCZOS)

                # 3. Prepare save arguments
                save_kwargs = {"format": "JPEG", "quality": 85, "optimize": True}
                if exif_data:
                    save_kwargs["exif"] = exif_data

                # 4. Save with EXIF
                img.save(output_path, **save_kwargs)

            print(f"Saved: {last_file} (Kept from a series of {len(group)})")
        except Exception as e:
            print(f"Error processing {last_file}: {e}")

    for filename in files:
        filepath = os.path.join(input_folder, filename)

        try:
            with Image.open(filepath) as img:
                # Only copy over photos from my phone
                exif = img.getexif()
                # EXIF tag 272 is the industry standard for 'Model'
                model = exif.get(272) if exif else None
                if not model or ("Pixel 6" not in str(model) and "Pixel 10 Pro" not in str(model)):
                    print(f"Skipping {filename} -> Model is '{model}'")
                    continue

                curr_hash = imagehash.dhash(img)

        except Exception as e:
            print(f"Could not read {filename}, skipping. Error: {e}")
            continue

        if not current_group:
            current_group.append(filename)
            prev_hash = curr_hash
        else:
            hash_difference = curr_hash - prev_hash

            if hash_difference <= hash_cutoff:
                current_group.append(filename)
                prev_hash = curr_hash
            else:
                save_last_of_group(current_group)
                current_group = [filename]
                prev_hash = curr_hash

    if current_group:
        save_last_of_group(current_group)

    (Path(output_folder) / "NOT_SCREENED").touch()
    print("Processing complete!")


@click.command()
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=str))
@click.argument("output_dir", type=click.Path(path_type=str))
def run(input_dir: str, output_dir: str) -> None:
    """
    Downsize images for the web, skipping adjacent near-duplicates.

    The final image from a series of near-duplicates is used. Retains EXIF data.

    Only copies photos from my phone models (e.g. Pixel 6 or Pixel 10 Pro). Skips WhatsApp, screenshots, etc.
    """
    process_photos(input_dir, output_dir)


if __name__ == "__main__":
    run()
