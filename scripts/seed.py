"""
Backfill planning: list the APKPure versions that don't yet have a complete
`app/<v>` release (one carrying a `metadata.json` asset). Re-runnable — already
published versions drop off the list.

  Locally, sequentially:
      for v in $(scripts/seed.py --list); do
        scripts/scrape.py --only "$v" --push --no-commit
      done
      scripts/reindex.py --push

  In CI, one runner per version (see .github/workflows/seed.yml):
      scripts/seed.py --emit-matrix
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from apkpure import fetch_versions
from ghrelease import list_releases
from report import ver_key

REPO_ROOT = Path(__file__).resolve().parent.parent


def _published() -> set[str]:
    """Versions whose app/<v> release already has its metadata.json asset."""
    done = set()
    for rel in list_releases():
        tag = rel["tag_name"]
        if tag.startswith("app/") and any(a["name"] == "metadata.json" for a in rel["assets"]):
            done.add(tag.split("/", 1)[1])
    return done


def pending() -> list[str]:
    remote = [v["version"] for v in fetch_versions()]
    done = _published()
    return sorted((v for v in remote if v not in done), key=ver_key)


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
