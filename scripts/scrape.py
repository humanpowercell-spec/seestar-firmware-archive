"""
Daily scrape: find Seestar app versions on APKPure that aren't in the inventory
yet, download each one, extract the firmware bundles, and publish a GitHub
Release per app version.

Per new app version, release `app/<version>` gets:
  • rootfs-iscope.tar.zst        full extracted armhf tree (RPi-based S30)
  • rootfs-iscope_64.tar.zst     full extracted aarch64 tree (RK3x S30/S30 Pro), when present
  • Seestar_<fwver>.bin          the ESP32 image(s), loose
  • <tracked binaries>           zwoair_imager, AM_Test, ... loose, arch-suffixed on collision
  • manifest.json                every file: path -> sha256/size/mode/container

Distinct ESP32 images additionally get an `esp32/<fwver>` release with the .bin.

The small stuff (inventory/*.json, inventory/manifests/*.json.gz,
inventory/metadata/*.json, HISTORY.md) is committed back to the repo.

Env: GITHUB_REPOSITORY, GITHUB_TOKEN. Runner needs ~3 GB free disk per new
version (XAPK download + extracted tree); a no-new-versions run does nothing.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from apkpure import fetch_versions, download_xapk
from extract import (
    find_firmware_bundles, extract_bundle_tree, esp32_bins, tracked_files,
    make_tar_zst,
)
from ghrelease import ensure_release, upload_asset, list_assets
from report import ver_key, regenerate

REPO_ROOT = Path(__file__).resolve().parent.parent
INV_DIR = REPO_ROOT / "inventory"
INV_PATH = INV_DIR / "fw_inventory.json"
BLOB_INDEX_PATH = INV_DIR / "blob_index.json"
MANIFEST_DIR = INV_DIR / "manifests"
METADATA_DIR = INV_DIR / "metadata"
WORK = REPO_ROOT / "_work"

ARCH_OF = {"assets/iscope": "armhf", "assets/iscope_64": "aarch64"}


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _loose_asset_names(trees: dict[str, Path]) -> dict[str, Path]:
    """
    {asset_name: source_path} for every tracked file. Naming:
      <base>                 basename is one single build everywhere
      <base>.<arch>          differs across arches, one build per arch
      <base>.<arch>.<sha12>  multiple distinct builds within an arch
    Byte-identical files collapse to a single asset.
    """
    hits: dict[str, list[tuple[str, Path, str]]] = {}
    for bundle, root in trees.items():
        arch = ARCH_OF.get(bundle, "x")
        for p in tracked_files(root):
            hits.setdefault(p.name, []).append((arch, p, _sha256_file(p)))

    out: dict[str, Path] = {}
    for base, group in hits.items():
        all_shas = {s for _, _, s in group}
        arches = {a for a, _, _ in group}
        for arch, path, sha in group:
            per_arch_shas = {s for a, _, s in group if a == arch}
            if len(all_shas) == 1:
                name = base
            elif len(per_arch_shas) == 1 and len(arches) > 1:
                name = f"{base}.{arch}"
            else:
                name = f"{base}.{arch}.{sha[:12]}"
            out.setdefault(name, path)
    return out


def _update_blob_index(blob_index: dict, ver: str, manifests: dict) -> None:
    for bundle, entries in manifests.items():
        arch = ARCH_OF.get(bundle, bundle.split("/")[-1])
        for e in entries:
            sha = e.get("sha256")
            if not sha:
                continue
            rec = blob_index.setdefault(sha, {
                "size": e["size"], "first_app_version": ver,
                "app_versions": [], "arches": [], "basenames": [], "paths": [],
            })
            if ver not in rec["app_versions"]:
                rec["app_versions"].append(ver)
            if arch not in rec["arches"]:
                rec["arches"].append(arch)
            base = e["path"].rsplit("/", 1)[-1]
            if base not in rec["basenames"]:
                rec["basenames"].append(base)
            if e["path"] not in rec["paths"]:
                rec["paths"].append(e["path"])
            if ver_key(ver) < ver_key(rec["first_app_version"]):
                rec["first_app_version"] = ver


def process_version(ver: str, url: str, inventory: dict, blob_index: dict,
                    args) -> None:
    print(f"\n=== app {ver} ===")
    vwork = WORK / ver
    apk_dir, trees_dir, assets_dir = vwork / "apk", vwork / "trees", vwork / "assets"
    # clear stale extract/stage dirs, but keep any pre-placed apk/ for --skip-download
    for d in (trees_dir, assets_dir):
        if d.exists():
            shutil.rmtree(d)
    if not args.skip_download and apk_dir.exists():
        shutil.rmtree(apk_dir)

    entry = inventory.setdefault(ver, {})
    entry.update({"version": ver, "download_url": url})

    if args.skip_download:
        xapk = apk_dir / f"Seestar_{ver}_APKPure.xapk"
        if not xapk.exists():
            print("  --skip-download and not on disk; skipping")
            return
    else:
        try:
            xapk = download_xapk(ver, url, apk_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"  download failed: {exc}")
            entry["download_error"] = str(exc)
            _save_json(INV_PATH, inventory)
            return

    entry["xapk_size"] = xapk.stat().st_size
    bundles = find_firmware_bundles(xapk)
    if not bundles:
        print("  no iscope/iscope_64 bundle in this version")
        entry.update({"bundles": {}, "scanned": True,
                      "scanned_at": datetime.now(timezone.utc).isoformat()})
        entry.pop("download_error", None)
        _save_json(INV_PATH, inventory)
        if not args.keep_xapk:
            xapk.unlink(missing_ok=True)
        return

    trees: dict[str, Path] = {}
    manifests: dict[str, list[dict]] = {}
    bundle_meta: dict[str, dict] = {}
    esp32_meta: dict[str, str] = {}
    assets: list[tuple[Path, str, str]] = []  # (path, asset_name, content_type)

    for bundle, blob in bundles.items():
        short = bundle.split("/")[-1]
        print(f"  {bundle} ({len(blob):,} bytes)")
        tree = trees_dir / short
        manifest, sig_ok = extract_bundle_tree(blob, tree)
        trees[bundle] = tree
        manifests[bundle] = manifest

        rootfs = assets_dir / f"rootfs-{short}.tar.zst"
        make_tar_zst(tree, rootfs, level=args.zstd_level)
        assets.append((rootfs, rootfs.name, "application/zstd"))

        bins = esp32_bins(manifest)
        for b in bins:
            dst = assets_dir / f"Seestar_{b['firmware_version']}.bin"
            if not dst.exists():
                shutil.copy2(tree / b["path"], dst)
                assets.append((dst, dst.name, "application/octet-stream"))
            esp32_meta[b["firmware_version"]] = dst.name

        file_count = sum(1 for e in manifest if "sha256" in e)
        bundle_meta[bundle] = {
            "signature_valid": sig_ok,
            "file_count": file_count,
            "rootfs_asset": rootfs.name,
            "rootfs_size": rootfs.stat().st_size,
        }
        note = "signed OK" if sig_ok else "SIGNATURE INVALID"
        fwv = ", ".join(sorted({b["firmware_version"] for b in bins})) or "none"
        print(f"    {file_count} files, ESP32 fw {fwv}, {note}, "
              f"rootfs {rootfs.stat().st_size / 1e6:.0f} MB")

    # loose tracked binaries
    loose = _loose_asset_names(trees)
    tracked_asset_names = sorted(loose)
    for name in tracked_asset_names:
        dst = assets_dir / name
        if not dst.exists():
            shutil.copy2(loose[name], dst)
        assets.append((dst, name, "application/octet-stream"))
    print(f"  {len(tracked_asset_names)} loose tracked binaries")

    # manifest.json (combined) as a release asset + gzipped in-repo
    combined_manifest = {b: manifests[b] for b in manifests}
    manifest_asset = assets_dir / "manifest.json"
    manifest_asset.write_text(json.dumps(combined_manifest, indent=1))
    assets.append((manifest_asset, "manifest.json", "application/json"))

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    _mbuf = io.BytesIO()
    with gzip.GzipFile(fileobj=_mbuf, mode="wb", mtime=0) as _gz:
        _gz.write(json.dumps(combined_manifest, indent=1).encode())
    (MANIFEST_DIR / f"{ver}.json.gz").write_bytes(_mbuf.getvalue())

    # inventory entry
    entry.update({
        "scanned": True,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "release_tag": f"app/{ver}",
        "bundles": bundle_meta,
        "esp32_firmware": sorted(esp32_meta, key=ver_key),
        "esp32_assets": esp32_meta,
        "tracked_assets": tracked_asset_names,
    })
    entry.pop("download_error", None)
    entry.pop("scan_error", None)
    _update_blob_index(blob_index, ver, manifests)

    # metadata.json as a release asset too — reindex.py rebuilds the whole
    # in-repo inventory purely from these small per-release assets, so the
    # matrix jobs never have to commit (and never race on the JSON files).
    meta_asset = assets_dir / "metadata.json"
    meta_asset.write_text(json.dumps(entry, indent=2, sort_keys=True))
    assets.append((meta_asset, "metadata.json", "application/json"))

    # publish
    if not args.no_publish:
        publish(ver, entry, assets, esp32_meta, assets_dir, inventory, args)

    _save_json(INV_PATH, inventory)
    _save_json(BLOB_INDEX_PATH, blob_index)
    _save_json(METADATA_DIR / f"{ver}.json", entry)

    if not args.keep_work:
        shutil.rmtree(vwork, ignore_errors=True)


def publish(ver: str, entry: dict, assets, esp32_meta: dict, assets_dir: Path,
            inventory: dict, args) -> None:
    tag = f"app/{ver}"
    fws = ", ".join(entry["esp32_firmware"]) or "none"
    sig = all(b["signature_valid"] for b in entry["bundles"].values())
    body = "\n".join([
        f"Seestar Android app **{ver}** — firmware bundles extracted from the APKPure release.",
        "",
        f"- ESP32 mount firmware: **{fws}**",
        f"- Bundles: {', '.join(entry['bundles'])}",
        f"- Signature: {'valid' if sig else 'INVALID'} (RSA-1024, ZWO's known key)",
        f"- Source: {entry.get('download_url', '?')}",
        "",
        "`rootfs-*.tar.zst` is the full extracted root filesystem. Loose assets are "
        "the tracked binaries for direct download. `manifest.json` maps every path "
        "to its SHA-256. Rebuild with `scripts/restore.py`.",
    ])
    rel = ensure_release(tag, f"Seestar app {ver}", body,
                         target_commitish=args.commitish or None)
    have = list_assets(rel)
    small = {"manifest.json", "metadata.json"}  # always refresh these
    for path, name, ctype in assets:
        existing = have.get(name)
        if existing and existing["size"] == path.stat().st_size and name not in small:
            print(f"    keep   {name} (already uploaded)")
            continue
        print(f"    upload {name} ({path.stat().st_size:,} B)")
        upload_asset(rel, path, name, clobber=True, content_type=ctype)

    for fwv, asset_name in esp32_meta.items():
        etag = f"esp32/{fwv}"
        seen = sorted(
            {ver, *(v for v, e in inventory.items()
                    if fwv in (e.get("esp32_firmware") or []))},
            key=ver_key,
        )
        ebody = "\n".join([
            f"ESP32 mount firmware **{fwv}** for the ZWO Seestar S30.",
            "",
            f"Shipped in app version(s): {', '.join(seen)}",
            "",
            "Extracted from the signed `assets/iscope` bundle. See the "
            f"`app/{seen[0]}` release for the full firmware tree.",
        ])
        erel = ensure_release(etag, f"ESP32 firmware {fwv}", ebody,
                              target_commitish=args.commitish or None)
        upload_asset(erel, assets_dir / asset_name, f"Seestar_{fwv}.bin",
                     clobber=True)


def git_commit(versions: list[str], args) -> None:
    paths = ["inventory", "HISTORY.md"]
    subprocess.run(["git", "-C", str(REPO_ROOT), "add", *paths], check=True)
    diff = subprocess.run(["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("no inventory changes to commit")
        return
    msg = f"archive: {', '.join(versions)}" if versions else "archive: refresh"
    subprocess.run(["git", "-C", str(REPO_ROOT), "commit", "-m", msg,
                    "-m", "[skip ci]"], check=True)
    if args.push:
        subprocess.run(["git", "-C", str(REPO_ROOT), "push"], check=True)
    for v in versions:
        tag = f"app/{v}"
        # first-write-wins: an app/<v> tag already upstream is left untouched
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "tag", tag],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  tag {tag} exists locally, leaving it")
            continue
        if args.push:
            p = subprocess.run(["git", "-C", str(REPO_ROOT), "push", "origin", tag],
                               capture_output=True, text=True)
            if p.returncode != 0:
                print(f"  tag {tag} push skipped: {p.stderr.strip()}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", metavar="VER",
                    help="process exactly these versions (ignores the inventory diff)")
    ap.add_argument("--max-versions", type=int, default=0,
                    help="cap how many new versions to process this run (0 = no cap)")
    ap.add_argument("--rescan", action="store_true", help="reprocess versions already scanned")
    ap.add_argument("--skip-download", action="store_true",
                    help="use XAPKs already under _work/<ver>/apk/, don't fetch")
    ap.add_argument("--keep-xapk", action="store_true")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--no-publish", action="store_true",
                    help="extract + update inventory only, don't touch GitHub Releases")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--commitish", default="", help="target branch/sha for new release tags")
    ap.add_argument("--zstd-level", type=int, default=19)
    args = ap.parse_args()

    inventory = _load(INV_PATH, {})
    blob_index = _load(BLOB_INDEX_PATH, {})

    remote = fetch_versions()
    print(f"APKPure: {len(remote)} version(s)")

    if args.only:
        wanted = [(v["version"], v["download_url"]) for v in remote
                  if v["version"] in args.only]
        missing = set(args.only) - {v for v, _ in wanted}
        if missing:
            print(f"warning: not offered by APKPure: {', '.join(sorted(missing))}")
    else:
        wanted = [
            (v["version"], v["download_url"]) for v in remote
            if args.rescan or v["version"] not in inventory
            or not inventory[v["version"]].get("scanned")
        ]
    wanted.sort(key=lambda kv: ver_key(kv[0]))
    if args.max_versions:
        wanted = wanted[:args.max_versions]

    if not wanted:
        print("nothing new")
        regenerate()
        return 0

    print(f"processing: {', '.join(v for v, _ in wanted)}")
    done = []
    for ver, url in wanted:
        try:
            process_version(ver, url, inventory, blob_index, args)
            done.append(ver)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR on {ver}: {exc}", file=sys.stderr)
            inventory.setdefault(ver, {})["scan_error"] = str(exc)
            _save_json(INV_PATH, inventory)

    regenerate()
    if not args.no_commit and done:
        git_commit(done, args)
    print(f"\ndone: {', '.join(done) or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
