"""
Minimal GitHub Releases client over the REST API (stdlib + requests only, no
`gh` binary needed). Works locally and in Actions.

Env:
  GITHUB_REPOSITORY   "owner/repo"   (set automatically in Actions)
  GITHUB_TOKEN        token with `contents: write`
  GITHUB_API_URL      optional, defaults to https://api.github.com
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
UPLOADS = os.environ.get("GITHUB_UPLOAD_URL", "https://uploads.github.com").rstrip("/")


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY not set (expected 'owner/repo')")
    return repo


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _req(method: str, url: str, timeout: int = 60, retry: bool = True,
         **kw) -> requests.Response:
    attempts = 4 if retry else 1
    for attempt in range(attempts):
        r = requests.request(method, url, headers=_headers(), timeout=timeout, **kw)
        if retry and r.status_code in (403, 429) and "rate limit" in r.text.lower():
            wait = 2 ** attempt * 15
            print(f"  rate limited, sleeping {wait}s")
            time.sleep(wait)
            continue
        return r
    return r


def get_release(tag: str) -> dict | None:
    r = _req("GET", f"{API}/repos/{_repo()}/releases/tags/{tag}")
    return r.json() if r.status_code == 200 else None


def list_releases() -> list[dict]:
    out, url = [], f"{API}/repos/{_repo()}/releases?per_page=100"
    while url:
        r = _req("GET", url)
        r.raise_for_status()
        out.extend(r.json())
        url = r.links.get("next", {}).get("url")
    return out


def ensure_release(tag: str, name: str, body: str, prerelease: bool = False,
                   target_commitish: str | None = None) -> dict:
    """Get the release for `tag`, creating it (and the tag) if absent; update notes if present."""
    existing = get_release(tag)
    payload = {"tag_name": tag, "name": name, "body": body, "prerelease": prerelease}
    if target_commitish:
        payload["target_commitish"] = target_commitish

    if existing:
        r = _req("PATCH", f"{API}/repos/{_repo()}/releases/{existing['id']}",
                 json={"name": name, "body": body, "prerelease": prerelease})
        r.raise_for_status()
        return r.json()

    r = _req("POST", f"{API}/repos/{_repo()}/releases", json=payload)
    r.raise_for_status()
    return r.json()


def list_assets(release: dict) -> dict[str, dict]:
    """{asset_name: asset} across all pages."""
    out: dict[str, dict] = {}
    url = f"{API}/repos/{_repo()}/releases/{release['id']}/assets?per_page=100"
    while url:
        r = _req("GET", url)
        r.raise_for_status()
        for a in r.json():
            out[a["name"]] = a
        url = r.links.get("next", {}).get("url")
    return out


def upload_asset(release: dict, path: Path, name: str | None = None,
                 clobber: bool = True, content_type: str = "application/octet-stream") -> dict:
    name = name or path.name
    if clobber:
        existing = list_assets(release).get(name)
        if existing:
            _req("DELETE", f"{API}/repos/{_repo()}/releases/assets/{existing['id']}")

    upload_url = release["upload_url"].split("{")[0]
    size = path.stat().st_size
    with open(path, "rb") as fh:
        r = _req("POST", f"{upload_url}?name={name}", data=fh, retry=False,
                 timeout=max(300, size // 200_000),  # ~200 KB/s worst-case floor
                 headers={**_headers(), "Content-Type": content_type})
    r.raise_for_status()
    return r.json()


def download_asset(release: dict, name: str, dest: Path) -> Path:
    asset = list_assets(release).get(name)
    if asset is None:
        raise FileNotFoundError(f"{name} not in release {release['tag_name']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(asset["url"], stream=True, timeout=120,
                      headers={**_headers(), "Accept": "application/octet-stream"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return dest
