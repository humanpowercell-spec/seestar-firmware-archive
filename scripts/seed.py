"""
One-time backfill helper.

The daily scrape processes every version APKPure offers that isn't in the
inventory, so the very first run *is* the backfill — but downloading ~20 XAPKs
(~1 GB each) plus building their trees will blow a single runner's disk. Two ways
to seed instead:

  1. Locally, sequentially, keeping disk in check:
        for v in $(scripts/seed.py --list); do
          scripts/scrape.py --only "$v" --push
        done

  2. In CI, one runner per version (see .github/workflows/seed.yml):
        scripts/seed.py --emit-matrix

`--from-local` imports an already-built store instead of re-downloading — point
it at seestar-s30-re/firmware/historical (fw_inventory.json + manifests/) to
carry over what's already been extracted, then only the release-asset upload
still needs the bundles.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from apkpure import fetch_versions
from report import ver_key

REPO_ROOT = Path(__file__).resolve().parent.parent
INV_PATH = REPO_ROOT / "inventory" / "fw_inventory.json"


def pending() -> list[str]:
    inv = json.loads(INV_PATH.read_text()) if INV_PATH.exists() else {}
    remote = [v["version"] for v in fetch_versions()]
    return sorted((v for v in remote
                   if v not in inv or not inv[v].get("scanned")), key=ver_key)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="one pending version per line")
    g.add_argument("--emit-matrix", action="store_true",
                   help="GitHub Actions matrix JSON: {\"version\": [...]}")
    args = ap.parse_args()

    todo = pending()
    if args.list:
        print("\n".join(todo))
    else:
        out = json.dumps({"version": todo})
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as fh:
                fh.write(f"matrix={out}\n")
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
