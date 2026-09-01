# Seestar Firmware Version History

_Generated 2026-09-01T17:06:06.116298+00:00 — do not edit, produced by `scripts/report.py`._

Reconstructed from the `assets/iscope` / `assets/iscope_64` signed firmware
bundles embedded in every historical Seestar Android app release (APKPure).
Each app version is a GitHub Release tagged `app/<version>` with the fully
extracted root filesystem(s) as `rootfs-*.tar.zst`, the ESP32 image and the
tracked binaries attached loose, and `manifest.json`. Distinct ESP32 images
also get a friendly `esp32/<version>` release.

---

## ESP32 mount-firmware timeline

| Firmware | First app version | Shipped in app version(s) | Release |
|---|---|---|---|

---

## Tracked Pi-side binaries (deduplicated by content hash)

Every distinct build of the binaries/scripts this project has RE narrative
around. Raw bytes: the loose asset on any listed `app/<version>` release, or
inside that release's `rootfs-*.tar.zst` at the path in `manifest.json`.

---

## Per-app-version detail

| App version | Bundles | ESP32 fw | Files | Signature | Release |
|---|---|---|---:|---|---|

---

## Notes

- Signature verification is against the RSA-1024 key ZWO ships in `libopenssllib.so` — it confirms the bundle is well-formed and signed with that (publicly known, compromised) key, not that a third party hasn't tampered with it.
- `.deb` payloads in a bundle are both kept as-is and unpacked to `<name>.deb.extracted/` inside the rootfs archive.
- APKPure only retains app `1.20.0` and newer; anything older is not recoverable from this source.

