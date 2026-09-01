"""
Rebuild a firmware tree from the archive's GitHub Releases.

    scripts/restore.py 3.3.1 ./out                 # both bundles
    scripts/restore.py 3.3.1 ./out --bundle iscope_64
    scripts/restore.py 3.3.1 ./out --esp32-only    # just Seestar_<fwver>.bin
    scripts/restore.py 3.3.1 ./out --verify        # check every file vs manifest.json

Needs GITHUB_REPOSITORY + GITHUB_TOKEN for the API (public repo: any token, or
a read-only fine-grained one).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from extract import extract_tar_zst
from ghrelease import get_release, download_asset, list_assets

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_manifest(ver: str, rel) -> dict:
    local = REPO_ROOT / "inventory" / "manifests" / f"{ver}.json.gz"
    if local.exists():
        with gzip.open(local, "rt") as fh:
            return json.load(fh)
    tmp = Path("/tmp") / f"seestar-manifest-{ver}.json"
    download_asset(rel, "manifest.json", tmp)
    return json.loads(tmp.read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("version")
    ap.add_argument("dest", type=Path)
    ap.add_argument("--bundle", choices=["iscope", "iscope_64"],
                    help="only this bundle (default: all present)")
    ap.add_argument("--esp32-only", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    tag = f"app/{args.version}"
    rel = get_release(tag)
    if rel is None:
        print(f"no release {tag}", file=sys.stderr)
        return 1
    args.dest.mkdir(parents=True, exist_ok=True)
    asset_names = set(list_assets(rel))

    if args.esp32_only:
        got = [n for n in asset_names if n.startswith("Seestar_") and n.endswith(".bin")]
        for n in got:
            print(f"  {n}")
            download_asset(rel, n, args.dest / n)
        return 0 if got else 1

    shorts = [f"iscope_64", "iscope"] if not args.bundle else [args.bundle]
    extracted = []
    for short in shorts:
        aname = f"rootfs-{short}.tar.zst"
        if aname not in asset_names:
            continue
        tarball = args.dest / aname
        print(f"  download {aname}")
        download_asset(rel, aname, tarball)
        out = args.dest / short
        out.mkdir(exist_ok=True)
        print(f"  extract -> {out}")
        extract_tar_zst(tarball, out)
        tarball.unlink()
        extracted.append(short)

    if not extracted:
        print("no rootfs assets found", file=sys.stderr)
        return 1

    if args.verify:
        manifest = _load_manifest(args.version, rel)
        bad = 0
        for bundle, entries in manifest.items():
            short = bundle.split("/")[-1]
            if short not in extracted:
                continue
            for e in entries:
                if "sha256" not in e:
                    continue
                fp = args.dest / short / e["path"]
                if not fp.is_file():
                    print(f"  MISSING {short}/{e['path']}")
                    bad += 1
                    continue
                got = hashlib.sha256(fp.read_bytes()).hexdigest()
                if got != e["sha256"]:
                    print(f"  HASH MISMATCH {short}/{e['path']}")
                    bad += 1
        print(f"verify: {'OK' if bad == 0 else f'{bad} problem(s)'}")
        return 1 if bad else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
