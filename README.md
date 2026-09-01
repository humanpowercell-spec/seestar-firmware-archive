# seestar-firmware-archive

Versioned archive of every firmware binary ZWO has shipped for the **Seestar S30**
smart telescope, reconstructed from the Android app.

Every Seestar Android release bundles the complete device firmware inside the APK
(`assets/iscope` for the RPi-based S30, `assets/iscope_64` for the newer RK3x
S30 / S30 Pro) as a signed `bzip2(tar)` payload. This repo scrapes every
historical app version off APKPure, verifies and unpacks those bundles, and
publishes one **GitHub Release per app version** with the fully extracted trees.

- **History + per-binary changelog:** [`HISTORY.md`](HISTORY.md) (generated)
- **Releases:** <https://github.com/humanpowercell-spec/seestar-firmware-archive/releases>

## What's in each release

`app/<version>` (e.g. `app/3.3.1`):

| Asset | What |
|---|---|
| `rootfs-iscope.tar.zst` | full extracted armhf root filesystem — every binary, script, `.ko`, calibration blob; `.deb`s unpacked to `*.deb.extracted/` |
| `rootfs-iscope_64.tar.zst` | same, aarch64 (newer hardware; absent on old versions) |
| `Seestar_<fwver>.bin` | the ESP32 mount-controller image, loose |
| `zwoair_imager`, `zwoair_guider`, `AM_Test`, `air_ble`, … | tracked binaries, loose, arch-suffixed on collision |
| `manifest.json` | every file → `sha256` / size / mode / source container |

`esp32/<fwver>` (e.g. `esp32/2.3.7`): just the ESP32 `.bin`, with release notes
listing which app versions shipped it.

## Why releases instead of Git / LFS

Release assets don't count against repo size, have a 2 GB/file limit (our largest
blob is ~150 MB), and download unmetered without auth. Git can't hold the
>100 MB blobs; LFS would cost money at this volume. The repo itself only tracks
the small stuff: `inventory/` JSON, gzipped per-version manifests, `HISTORY.md`.

## Layout

```
inventory/
  fw_inventory.json        one record per app version (bundles, signature, assets)
  blob_index.json          sha256 -> {size, first/all app versions, basenames, paths}
  manifests/<ver>.json.gz  full path->sha256 listing per app version
  metadata/<ver>.json      the fw_inventory record for one version, standalone
scripts/
  scrape.py                daily entrypoint: diff APKPure, extract, publish
  extract.py               signed bundle -> real file tree + manifest
  ghrelease.py             GitHub Releases over the REST API (no gh binary)
  report.py                regenerate HISTORY.md from the inventory
  restore.py               rebuild a version's tree from its release
  seed.py                  backfill planning / matrix emission
.github/workflows/
  daily-scrape.yml         06:17 UTC cron + manual
  seed.yml                 one-time backfill, one runner per version
```

Bundle discovery, signature verification, the APKPure API client and the
resumable XAPK downloader are imported from
[`seestar-shell`](https://github.com/humanpowercell-spec/seestar-shell) —
single source of truth, pinned in `requirements.txt`.

## Local use

```bash
pip install -r requirements.txt
export GITHUB_REPOSITORY=humanpowercell-spec/seestar-firmware-archive
export GITHUB_TOKEN=<contents:write token>

python scripts/scrape.py                      # process anything new, commit, no push
python scripts/scrape.py --only 3.3.1 --no-publish   # extract one version, inspect _work/
python scripts/restore.py 3.3.1 ./out --verify       # rebuild + checksum a version
```

## Provenance / caveats

- Signatures verify against the RSA-1024 key ZWO ships (publicly) in
  `libopenssllib.so` — confirms well-formed + signed with that key, not third-party
  integrity.
- APKPure retains app `1.20.0` onward (ESP32 `1.7.5` onward). Older firmware is
  not recoverable from this source.
- Not affiliated with or endorsed by ZWO. Archival / interoperability / security
  research use.
