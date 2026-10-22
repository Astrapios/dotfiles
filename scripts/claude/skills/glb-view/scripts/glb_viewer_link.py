#!/usr/bin/env python3
"""Upload GLB files to the user's S3 prefix and print a ready GLB-viewer link.

Usage:
    glb_viewer_link.py --topic <topic> file1.glb [file2.glb ...]        # upload + link
    glb_viewer_link.py --link s3://bucket/key1.glb s3://bucket/key2.glb  # link only
Options: --range 100 (viewer altitude colour range), --no-upload (same as --link with files)

Gotchas baked in (each cost a session once):
  - DEFAULT aws profile only: the EC2 instance role has valid creds. `--profile dev` expires,
    `--no-sign-request` is rejected on write (bucket is KMS-encrypted). Reads are anonymous.
  - The viewer permalink parameter is `models` (comma-separated, each URI URL-encoded, comma as
    %2C). `model_paths` is only the /get_models_zip endpoint. `model_visibility=1,...` makes
    every model visible (the viewer hides some by default).
  - Large uploads: `aws configure set default.s3.multipart_threshold 8MB` if a multipart
    upload trips the anonymous path.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from urllib.parse import quote

BUCKET_PREFIX = "s3://array-labs-dev-test-data/data/jisu"
VIEWER = "https://glb-viewer.dev.arraylabs.net/"


def upload(path: str, topic: str) -> str:
    name = path.rsplit("/", 1)[-1]
    uri = f"{BUCKET_PREFIX}/{topic}/{name}"
    r = subprocess.run(["aws", "s3", "cp", path, uri], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"upload failed for {path} (use the DEFAULT profile, no --profile dev, no --no-sign-request)")
    return uri


def link(uris: list[str], rng: int) -> str:
    models = "%2C".join(quote(u, safe="") for u in uris)
    vis = "%2C".join("1" for _ in uris)
    return f"{VIEWER}?models={models}&model_visibility={vis}&range={rng}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--topic", help="S3 sub-folder under data/jisu/ (required for upload)")
    ap.add_argument("--link", action="store_true", help="files are already s3:// URIs; print the link only")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--range", type=int, default=100)
    a = ap.parse_args()
    if a.link or a.no_upload or all(f.startswith("s3://") for f in a.files):
        uris = a.files
    else:
        if not a.topic:
            raise SystemExit("--topic is required to upload")
        uris = [upload(f, a.topic) for f in a.files]
        for u in uris:
            print("uploaded", u)
    print(link(uris, a.range))


if __name__ == "__main__":
    main()
