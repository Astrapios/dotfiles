#!/usr/bin/env python3
"""Upload GLB files to the user's S3 prefix and print a ready viewer link (the IPR explorer viewer by default).

Usage:
    glb_viewer_link.py --topic <topic> file1.glb [file2.glb ...]        # upload + explorer link
    glb_viewer_link.py --link s3://bucket/key1.glb s3://bucket/key2.glb  # link only
    glb_viewer_link.py --local outputs/x/a.glb outputs/x/b.las           # explorer link to files on this box, no upload
    glb_viewer_link.py --viewer glb --topic <topic> a.glb                # the GLB viewer (off the LAN, GIF / video)
Options: --labels a,b (default: the file names), --range (hue period in m; the GLB viewer defaults to 100, the explorer
colors by height over the data's range unless given), --base (explorer address), --no-upload (same as --link).

The explorer viewer (http://10.2.1.59:2720/viewer.html) opens every format the GLB viewer reads (GLB, glTF, LAS, LAZ,
CSV, GeoTIFF, depthmap pkl) from s3://, https:// or a path on this box. It is on this EC2 box's private address: the
link opens on the office network or VPN only, while `serve.sh` runs. s3:// files stay portable: the same models open in
the GLB viewer (--viewer glb) for anyone off the LAN.

Gotchas baked in (each cost a session once):
  - DEFAULT aws profile only: the EC2 instance role has valid creds. `--profile dev` expires,
    `--no-sign-request` is rejected on write (bucket is KMS-encrypted). Reads are anonymous.
  - The viewer permalink parameter is `models` (comma-separated, each URI URL-encoded, comma as
    %2C). `model_paths` is only the /get_models_zip endpoint. `model_visibility=1,...` makes
    every model visible (the GLB viewer hides some by default).
  - Large uploads: `aws configure set default.s3.multipart_threshold 8MB` if a multipart
    upload trips the anonymous path.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

BUCKET_PREFIX = "s3://array-labs-dev-test-data/data/jisu"
GLB_VIEWER = "https://glb-viewer.dev.arraylabs.net/"
EXPLORER = "http://10.2.1.59:2720"
PROJECT_ROOT = Path("/home/ubuntu/src/site3d_project_root")


def upload(path: str, topic: str) -> str:
    name = path.rsplit("/", 1)[-1]
    uri = f"{BUCKET_PREFIX}/{topic}/{name}"
    r = subprocess.run(["aws", "s3", "cp", path, uri], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"upload failed for {path} (use the DEFAULT profile, no --profile dev, no --no-sign-request)")
    return uri


def local_spec(path: str) -> str:
    """A file on this box as the explorer names it: relative to the project root when under it, else absolute."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"no such file: {path}")
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def glb_link(uris: list[str], labels: list[str], rng: int | None) -> str:
    models = "%2C".join(quote(u, safe="") for u in uris)
    vis = "%2C".join("1" for _ in uris)
    lab = "%7C".join(quote(s, safe="") for s in labels)
    return f"{GLB_VIEWER}?models={models}&model_labels={lab}&model_visibility={vis}&range={rng or 100}"


def explorer_link(specs: list[str], labels: list[str], rng: int | None, base: str) -> str:
    # '/' and ':' stay readable (paths, s3:// URIs); the list separators ',' and '|' inside a value are encoded
    models = ",".join(quote(s, safe="/:") for s in specs)
    lab = "|".join(quote(s, safe="") for s in labels)
    vis = ",".join("1" for _ in specs)
    return f"{base.rstrip('/')}/viewer.html?models={models}&model_labels={lab}&model_visibility={vis}" + (
        f"&range={rng}" if rng else ""
    )


def explorer_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=10) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--topic", help="S3 sub-folder under data/jisu/ (required for upload)")
    ap.add_argument("--link", action="store_true", help="files are already s3:// URIs; print the link only")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--local", action="store_true", help="explorer link to the files on this box, no upload")
    ap.add_argument("--viewer", choices=("explorer", "glb"), default="explorer")
    ap.add_argument("--labels", help="comma-separated, one per file (default: the file names)")
    ap.add_argument("--range", type=int, default=None)
    ap.add_argument("--base", default=EXPLORER, help="explorer address (default %(default)s)")
    a = ap.parse_args()
    if a.local and a.viewer == "glb":
        raise SystemExit("--local files open in the explorer only (the GLB viewer reads s3:// and https://)")
    if a.local:
        specs = [local_spec(f) for f in a.files]
    elif a.link or a.no_upload or all(f.startswith(("s3://", "http://", "https://")) for f in a.files):
        specs = a.files
    else:
        if not a.topic:
            raise SystemExit("--topic is required to upload (or --local for files on this box)")
        specs = [upload(f, a.topic) for f in a.files]
        for u in specs:
            print("uploaded", u)
    labels = a.labels.split(",") if a.labels else [s.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0] for s in specs]
    if len(labels) != len(specs):
        raise SystemExit(f"{len(labels)} labels for {len(specs)} files")
    if a.viewer == "glb":
        print(glb_link(specs, labels, a.range))
        return
    if not explorer_up(a.base):
        sys.stderr.write(f"warning: the explorer at {a.base} does not answer /health (start it: sar_apps/ipr_explorer/serve.sh start)\n")
    print(explorer_link(specs, labels, a.range, a.base))


if __name__ == "__main__":
    main()
