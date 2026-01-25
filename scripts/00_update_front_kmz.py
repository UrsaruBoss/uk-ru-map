#!/usr/bin/env python3
"""
Download and extract the latest KMZ frontline map.

Outputs:
  - assets/doc.kml
  - assets/images/
Optional:
  - assets/latest.kmz (cached)
"""

from __future__ import annotations

import argparse
import os
import shutil
import zipfile
from pathlib import Path

import requests


DEFAULT_URL = "https://raw.githubusercontent.com/owlmaps/UAControlMapBackups/master/latest.kmz"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def download_file(url: str, out_path: Path, timeout: int = 60) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def extract_kmz(kmz_path: Path, extract_dir: Path) -> None:
    ensure_dir(extract_dir)
    with zipfile.ZipFile(kmz_path, "r") as z:
        z.extractall(extract_dir)


def move_artifacts(extract_dir: Path, assets_dir: Path) -> None:
    kml_src = extract_dir / "doc.kml"
    images_src = extract_dir / "images"

    if not kml_src.exists():
        raise FileNotFoundError(f"Missing doc.kml after extraction in {extract_dir}")

    ensure_dir(assets_dir)

    # move doc.kml
    shutil.copy2(kml_src, assets_dir / "doc.kml")

    # move images folder if exists
    if images_src.exists() and images_src.is_dir():
        images_dst = assets_dir / "images"
        if images_dst.exists():
            shutil.rmtree(images_dst)
        shutil.copytree(images_src, images_dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="KMZ URL to download")
    parser.add_argument("--assets", default="assets", help="Assets output directory")
    parser.add_argument("--kmz", default=None, help="Local KMZ path (skip download)")
    parser.add_argument("--timeout", type=int, default=60, help="Download timeout in seconds")
    args = parser.parse_args()

    assets_dir = Path(args.assets)
    ensure_dir(assets_dir)

    # Determine KMZ source
    if args.kmz:
        kmz_path = Path(args.kmz)
        if not kmz_path.exists():
            print(f"ERROR: local KMZ not found: {kmz_path}")
            return 2
    else:
        kmz_path = assets_dir / "latest.kmz"
        try:
            print(f"Downloading KMZ: {args.url}")
            download_file(args.url, kmz_path, timeout=args.timeout)
        except Exception as e:
            print(f"ERROR: download failed: {e}")
            return 2

    # Extract to temp folder
    tmp_dir = assets_dir / "_tmp_kmz_extract"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    try:
        print(f"Extracting: {kmz_path}")
        extract_kmz(kmz_path, tmp_dir)

        print("Collecting artifacts")
        move_artifacts(tmp_dir, assets_dir)

        print("Done. Updated assets/doc.kml and assets/images/")
        return 0
    except zipfile.BadZipFile:
        print("ERROR: KMZ is not a valid zip archive (corrupted file).")
        return 2
    except Exception as e:
        print(f"ERROR: extraction failed: {e}")
        return 2
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    raise SystemExit(main())
