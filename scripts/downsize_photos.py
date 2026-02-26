import argparse
import os

import imagehash
from PIL import Image


"""
Downsize a directory of photos. Skip duplicates/near-duplicates, just using the last one in a series.
Copies exif data. Only copies photos from my phone (no whatsapp photos, screenshots etc).
Run like this:
python downsize_photos.py /home/sara/Dropbox/Pictures/phone/2024/10 /home/sara/repos/travel-log/data/private/photos/2024/10
"""

def process_photos(input_folder, output_folder, max_size=(1920, 1920), hash_cutoff=18):
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

    def save_last_of_group(group):
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

    print("Processing complete!")

# --- Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Downsize images for the web, skip adjacent near-duplicates, and retain EXIF data."
    )

    parser.add_argument("input_dir", help="Path to the folder containing the original images.")
    parser.add_argument("output_dir", help="Path to the folder where downsized images will be saved.")

    args = parser.parse_args()
    process_photos(args.input_dir, args.output_dir)
