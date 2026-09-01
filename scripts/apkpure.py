"""
APKPure version list + resumable XAPK download for com.zwo.seestar.

Vendored from github.com/humanpowercell-spec/seestar-shell (seestar_shell/
inventory.py) so this repo is self-contained — its CI only needs the repo's own
GITHUB_TOKEN, no cross-repo clone. Keep in sync with upstream if the protobuf
response shape changes.
"""

from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

import requests

PACKAGE = "com.zwo.seestar"
API_URL = f"https://api.pureapk.com/m/v3/cms/app_version?hl=en-US&package_name={PACKAGE}"
CHUNK_SIZE = 65536

ANDROID_HEADERS = {
    "x-cv": "3172501",
    "x-sv": "29",
    "x-abis": "arm64-v8a,armeabi-v7a,armeabi,x86,x86_64",
    "x-gp": "1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "APKPure/3.17.25 (Linux; U; Android 10; Pixel 3 Build/QQ3A.200805.001)",
}


def fetch_versions() -> list[dict]:
    resp = requests.get(API_URL, headers=ANDROID_HEADERS, timeout=15)
    resp.raise_for_status()
    return _parse_versions(resp.content)


def _parse_versions(data: bytes) -> list[dict]:
    """The response is protobuf; decode latin-1 and positionally match, same as upstream."""
    text = data.decode("latin-1")
    ver_re = re.compile(r"\b([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})\b")
    url_re = re.compile(
        r"XAPKJ.{2}(https://download\.pureapk\.com/b/XAPK/[A-Za-z0-9_.\-/?=&%:+]+)"
    )
    ver_positions = [(m.start(), m.group(1)) for m in ver_re.finditer(text)]

    seen, versions = set(), []
    for cap in url_re.finditer(text):
        url_pos, url = cap.start(1), cap.group(1)
        version = next((v for pos, v in reversed(ver_positions) if pos < url_pos), None)
        if version and version not in seen:
            seen.add(version)
            versions.append({"version": version, "download_url": url})
    return versions


def download_xapk(version: str, url: str, dest_dir: Path) -> Path:
    """Download with HTTP Range resume; returns the local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"Seestar_{version}_APKPure.xapk"

    if dest.exists():
        try:
            zipfile.ZipFile(dest).close()
            print(f"  {dest.name} already complete ({dest.stat().st_size:,} bytes)")
            return dest
        except zipfile.BadZipFile:
            pass

    resume_from = dest.stat().st_size if dest.exists() else 0
    headers = dict(ANDROID_HEADERS)
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
        print(f"  resuming {dest.name} from {resume_from:,}")
    else:
        print(f"  downloading {dest.name}")

    resp = requests.get(url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0)) + resume_from
    received, t0, last = resume_from, time.time(), 0.0

    with open(dest, "ab" if resume_from else "wb") as f:
        for chunk in resp.iter_content(CHUNK_SIZE):
            f.write(chunk)
            received += len(chunk)
            now = time.time()
            if now - last >= 2.0:
                pct = received * 100 // total if total else 0
                rate = (received - resume_from) / (now - t0 or 1e-3) / 1_048_576
                print(f"\r    {received:,} / {total:,}  {pct}%  {rate:.1f} MB/s", end="", flush=True)
                last = now
    print(f"\n  done in {time.time() - t0:.0f}s")
    return dest
