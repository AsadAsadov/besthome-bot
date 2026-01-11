import argparse
import os
import re
from typing import Optional


def extract_drive_file_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise RuntimeError("Google Drive linkində file id tapılmadı")
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"/d/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    raise RuntimeError("Google Drive linkində file id tapılmadı")


def build_direct_download_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )


def save_last_link(url: str) -> None:
    output_path = os.path.join(os.path.dirname(__file__), "last_db_link.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(url)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a direct Google Drive download link."
    )
    parser.add_argument(
        "file_id_or_url",
        help="Google Drive file id or share URL",
    )
    args = parser.parse_args(argv)

    file_id = extract_drive_file_id(args.file_id_or_url)
    direct_url = build_direct_download_url(file_id)
    print(f"[GDRIVE] direct_link={direct_url}")
    save_last_link(direct_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
