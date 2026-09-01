"""
Regenerate HISTORY.md from inventory/fw_inventory.json + inventory/blob_index.json.

Pure function of the committed inventory — no network, no downloads. Run at the
end of every scrape, or by hand after editing the inventory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tracked import TRACKED_BASENAMES

REPO_ROOT = Path(__file__).resolve().parent.parent
INV_PATH = REPO_ROOT / "inventory" / "fw_inventory.json"
BLOB_INDEX_PATH = REPO_ROOT / "inventory" / "blob_index.json"
HISTORY_PATH = REPO_ROOT / "HISTORY.md"


def ver_key(v: str) -> list[int]:
    try:
        return [int(x) for x in v.split(".")]
    except ValueError:
        return [0]


def _fw_versions(entry: dict) -> list[str]:
    return list(entry.get("esp32_firmware") or [])


def build(inventory: dict, blob_index: dict) -> str:
    now = datetime.now(timezone.utc).isoformat()
    versions = sorted(inventory, key=ver_key)

    # ESP32 firmware timeline
    fw_seen: dict[str, list[str]] = {}
    for ver in versions:
        for fwv in _fw_versions(inventory[ver]):
            fw_seen.setdefault(fwv, []).append(ver)

    L = [
        "# Seestar Firmware Version History",
        "",
        f"_Generated {now} — do not edit, produced by `scripts/report.py`._",
        "",
        "Reconstructed from the `assets/iscope` / `assets/iscope_64` signed firmware",
        "bundles embedded in every historical Seestar Android app release (APKPure).",
        "Each app version is a GitHub Release tagged `app/<version>` with the fully",
        "extracted root filesystem(s) as `rootfs-*.tar.zst`, the ESP32 image and the",
        "tracked binaries attached loose, and `manifest.json`. Distinct ESP32 images",
        "also get a friendly `esp32/<version>` release.",
        "",
        "---",
        "",
        "## ESP32 mount-firmware timeline",
        "",
        "| Firmware | First app version | Shipped in app version(s) | Release |",
        "|---|---|---|---|",
    ]
    for fwv in sorted(fw_seen, key=ver_key):
        apps = fw_seen[fwv]
        L.append(f"| {fwv} | {apps[0]} | {', '.join(apps)} | `esp32/{fwv}` |")

    # Tracked Pi-side binaries, from the blob index
    L += [
        "",
        "---",
        "",
        "## Tracked Pi-side binaries (deduplicated by content hash)",
        "",
        "Every distinct build of the binaries/scripts this project has RE narrative",
        "around. Raw bytes: the loose asset on any listed `app/<version>` release, or",
        "inside that release's `rootfs-*.tar.zst` at the path in `manifest.json`.",
        "",
    ]
    by_name: dict[str, list[tuple[str, dict]]] = {}
    for sha, rec in blob_index.items():
        for bn in rec.get("basenames", []):
            if bn in TRACKED_BASENAMES:
                by_name.setdefault(bn, []).append((sha, rec))

    for bn in sorted(by_name):
        L.append(f"### `{bn}`")
        L.append("")
        L.append("| First app version | Arch | Size | SHA-256 | Seen in app version(s) |")
        L.append("|---|---|---:|---|---|")
        rows = sorted(by_name[bn],
                      key=lambda kv: (ver_key(kv[1]["first_app_version"]), kv[1].get("arches", [])))
        for sha, rec in rows:
            apps = ", ".join(sorted(rec.get("app_versions", []), key=ver_key))
            arch = ", ".join(rec.get("arches", [])) or "—"
            L.append(f"| {rec['first_app_version']} | {arch} | {rec['size']:,} | "
                     f"`{sha[:16]}` | {apps} |")
        L.append("")

    # Per-app-version detail
    L += [
        "---",
        "",
        "## Per-app-version detail",
        "",
        "| App version | Bundles | ESP32 fw | Files | Signature | Release |",
        "|---|---|---|---:|---|---|",
    ]
    for ver in versions:
        e = inventory[ver]
        bundles = e.get("bundles") or {}
        bnames = ", ".join(b.split("/")[-1] for b in bundles) or "none"
        fws = ", ".join(_fw_versions(e)) or "—"
        files = ", ".join(str(b.get("file_count", "?")) for b in bundles.values()) or "—"
        sigs = [b.get("signature_valid") for b in bundles.values()]
        sig = "valid" if sigs and all(sigs) else ("INVALID" if sigs else "—")
        if e.get("download_error"):
            sig = "download error"
        elif e.get("scan_error"):
            sig = "scan error"
        tag = e.get("release_tag", f"app/{ver}")
        L.append(f"| {ver} | {bnames} | {fws} | {files} | {sig} | `{tag}` |")

    L += [
        "",
        "---",
        "",
        "## Notes",
        "",
        "- Signature verification is against the RSA-1024 key ZWO ships in "
        "`libopenssllib.so` — it confirms the bundle is well-formed and signed with "
        "that (publicly known, compromised) key, not that a third party hasn't "
        "tampered with it.",
        "- `.deb` payloads in a bundle are both kept as-is and unpacked to "
        "`<name>.deb.extracted/` inside the rootfs archive.",
        "- APKPure only retains app `1.20.0` and newer; anything older is not "
        "recoverable from this source.",
        "",
    ]
    return "\n".join(L) + "\n"


def regenerate() -> Path:
    inventory = json.loads(INV_PATH.read_text()) if INV_PATH.exists() else {}
    blob_index = json.loads(BLOB_INDEX_PATH.read_text()) if BLOB_INDEX_PATH.exists() else {}
    HISTORY_PATH.write_text(build(inventory, blob_index))
    print(f"  wrote {HISTORY_PATH.relative_to(REPO_ROOT)}")
    return HISTORY_PATH


if __name__ == "__main__":
    regenerate()
