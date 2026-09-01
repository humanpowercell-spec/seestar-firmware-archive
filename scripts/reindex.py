"""
Rebuild the entire in-repo inventory from the published `app/*` releases, then
commit it in one shot.

The matrix seed / daily scrape jobs only create releases (each uploads a small
`metadata.json` + `manifest.json` alongside the firmware assets). This job is
the single writer of `inventory/` and `HISTORY.md`, so those files never race
or hit rebase conflicts across parallel version jobs.

Idempotent: run it any time to resync the repo to whatever releases exist.

Env: GITHUB_REPOSITORY, GITHUB_TOKEN.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import subprocess
import sys
from pathlib import Path

from ghrelease import list_releases, download_asset
from report import REPO_ROOT, ver_key, regenerate
from scrape import _update_blob_index, INV_PATH, BLOB_INDEX_PATH, MANIFEST_DIR, METADATA_DIR

INV_DIR = REPO_ROOT / "inventory"


def _download_text(rel: dict, name: str) -> str | None:
    tmp = Path("/tmp") / f"reindex-{rel['tag_name'].replace('/', '_')}-{name}"
    try:
        download_asset(rel, name, tmp)
    except FileNotFoundError:
        return None
    return tmp.read_text()


def rebuild() -> list[str]:
    releases = [r for r in list_releases() if r["tag_name"].startswith("app/")]
    print(f"{len(releases)} app/* release(s)")

    inventory: dict = {}
    blob_index: dict = {}
    versions: list[str] = []

    for rel in sorted(releases, key=lambda r: ver_key(r["tag_name"].split("/", 1)[1])):
        ver = rel["tag_name"].split("/", 1)[1]
        meta_txt = _download_text(rel, "metadata.json")
        man_txt = _download_text(rel, "manifest.json")
        if meta_txt is None or man_txt is None:
            print(f"  {ver}: missing metadata.json/manifest.json — skipping")
            continue

        entry = json.loads(meta_txt)
        manifests = json.loads(man_txt)
        inventory[ver] = entry
        versions.append(ver)

        _update_blob_index(blob_index, ver, manifests)
        (METADATA_DIR / f"{ver}.json").parent.mkdir(parents=True, exist_ok=True)
        (METADATA_DIR / f"{ver}.json").write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(manifests, indent=1).encode())
        (MANIFEST_DIR / f"{ver}.json.gz").write_bytes(buf.getvalue())
        print(f"  {ver}: {sum(len(v) for v in manifests.values())} manifest entries")

    # drop stale per-version files for releases that no longer exist
    keep = set(versions)
    for d, suffix in ((METADATA_DIR, ".json"), (MANIFEST_DIR, ".json.gz")):
        for p in d.glob(f"*{suffix}"):
            v = p.name[: -len(suffix)]
            if v not in keep and v != ".git":
                p.unlink()
                print(f"  removed stale {p.relative_to(REPO_ROOT)}")

    INV_PATH.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    BLOB_INDEX_PATH.write_text(json.dumps(blob_index, indent=2, sort_keys=True) + "\n")
    regenerate()
    return versions


def git_push_once(branch: str) -> None:
    R = str(REPO_ROOT)
    subprocess.run(["git", "-C", R, "add", "inventory", "HISTORY.md"], check=True)
    if subprocess.run(["git", "-C", R, "diff", "--cached", "--quiet"]).returncode == 0:
        print("inventory already up to date")
        return
    subprocess.run(["git", "-C", R, "commit", "-m", "reindex: resync inventory from releases",
                    "-m", "[skip ci]"], check=True)
    for attempt in range(5):
        p = subprocess.run(["git", "-C", R, "push", "origin", f"HEAD:{branch}"],
                           capture_output=True, text=True)
        if p.returncode == 0:
            print("pushed")
            return
        print(f"  push rejected (attempt {attempt + 1}): {p.stderr.strip()}")
        subprocess.run(["git", "-C", R, "fetch", "origin", branch], check=True)
        rb = subprocess.run(["git", "-C", R, "rebase", f"origin/{branch}", "-X", "theirs"],
                            capture_output=True, text=True)
        if rb.returncode != 0:
            subprocess.run(["git", "-C", R, "rebase", "--abort"])
            # our content is a pure function of the releases; just redo it on top
            subprocess.run(["git", "-C", R, "reset", "--hard", f"origin/{branch}"], check=True)
            rebuild()
            subprocess.run(["git", "-C", R, "add", "inventory", "HISTORY.md"], check=True)
            if subprocess.run(["git", "-C", R, "diff", "--cached", "--quiet"]).returncode == 0:
                print("inventory already up to date after rebase")
                return
            subprocess.run(["git", "-C", R, "commit", "-m",
                            "reindex: resync inventory from releases", "-m", "[skip ci]"], check=True)
    raise RuntimeError("could not push inventory after 5 attempts")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="commit + push the rebuilt inventory")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    versions = rebuild()
    print(f"\nrebuilt inventory for {len(versions)} version(s): {', '.join(versions)}")
    if args.push:
        git_push_once(args.branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
