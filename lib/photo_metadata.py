import os
from typing import Any, Optional

from PIL import Image


def get_image_metadata(file_path: str) -> dict[str, Any]:
    """Extracts camera model, timestamp, and rich GPS coordinates from an image."""
    metadata: dict[str, Any] = {
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

            # DateTimeOriginal (36867) lives in the Exif sub-IFD (0x8769), not IFD0.
            # img.getexif() only exposes IFD0 directly, so we must use get_ifd().
            # Other date fields exist but are intentionally not used as fallbacks:
            #   DateTimeDigitized (36868, Exif sub-IFD) — when the image was digitised
            #   DateTime (306, IFD0)                    — last modification time (e.g. after a crop)
            exif_ifd = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
            metadata["timestamp"] = exif_ifd.get(36867)
            if not metadata["timestamp"]:
                print(f"WARNING: {os.path.basename(file_path)}: no DateTimeOriginal in EXIF")

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
        print(f"Warning: could not read EXIF data for {os.path.basename(file_path)}: {e}")

    return metadata
