"""
Turn a signed Seestar firmware bundle (`assets/iscope` / `assets/iscope_64`,
format `bzip2(tar) || RSA-1024 signature`) into a real, browsable file tree on
disk plus a manifest.

Every regular file is written out at its real path. Any `.deb` found is written
as-is *and* its `data.tar.*` payload is unpacked to `<name>.deb.extracted/` so
individual Pi-side binaries (`zwoair_imager`, kernel modules, ...) are reachable
without a Debian toolchain. `.deb` payload decompression shells out to the
`zstd` / `xz` / `gzip` CLIs so there is no native Python build dependency.

Bundle discovery + signature verification logic is vendored from
seestar_shell/firmware.py (see github.com/humanpowercell-spec/seestar-shell) so
this repo stays self-contained.
"""

from __future__ import annotations

import bz2
import hashlib
import io
import os
import re
import subprocess
import tarfile
from pathlib import Path
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tracked import TRACKED_BASENAMES

BIN_RE = re.compile(r"(?:^|/)Seestar_([0-9]+\.[0-9]+\.[0-9]+)\.bin$")
DEB_RE = re.compile(r"\.deb$", re.IGNORECASE)
MAX_DEB_NESTING = 3

BUNDLE_NAMES = ["assets/iscope", "assets/iscope_64"]
SIG_LEN = 128  # RSA-1024 PKCS#1 v1.5 signature trailer

# Public half of the RSA-1024 key ZWO ships (privately usable, publicly known)
# in libopenssllib.so. Verification proves "well-formed + signed with this key",
# not third-party integrity — that key being extractable is the vulnerability.
_ZWO_PUBLIC_KEY = rsa.RSAPublicNumbers(
    e=65537,
    n=int(
        "ed12ecbaa16277d03f6be7b3bb1913cb387d5fd0253bf5b9ab10ff4bf182e6c1"
        "74207d13d379e81f9a652c547f733c914ec5d18fa998236ff53f730730bb7eee3"
        "848960c800d888093ab19ee9ac4bc93af1998517a6c802e872ba95f1c250b14a3"
        "8bac75f4592f882a09bed78bc79c0efedc87372c8ccafceaf21a4b6982f5e1",
        16,
    ),
).public_key()


def find_firmware_bundles(xapk_path: Path) -> dict[str, bytes]:
    """{bundle_name: raw_bytes} for whichever of BUNDLE_NAMES are inside the (X)APK."""
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(xapk_path) as outer:
        names = outer.namelist()
        is_xapk = "manifest.json" in names and any(n.endswith(".apk") for n in names)
        apk_list = ([n for n in names if n.endswith(".apk") and "/" not in n]
                    if is_xapk else [None])
        for apk_entry in apk_list:
            inner = zipfile.ZipFile(io.BytesIO(outer.read(apk_entry))) if apk_entry else outer
            try:
                inner_names = set(inner.namelist())
                for bn in BUNDLE_NAMES:
                    if bn in inner_names and bn not in found:
                        found[bn] = inner.read(bn)
            finally:
                if apk_entry:
                    inner.close()
    return found


def split_signed_bundle(blob: bytes) -> tuple[bytes, bytes]:
    return blob[:-SIG_LEN], blob[-SIG_LEN:]


def verify_signature(bz2_body: bytes, signature: bytes) -> bool:
    try:
        _ZWO_PUBLIC_KEY.verify(signature, bz2_body, padding.PKCS1v15(), hashes.SHA1())
        return True
    except InvalidSignature:
        return False


def open_tar(bz2_body: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(bz2.decompress(bz2_body)))


# ── path safety ──────────────────────────────────────────────────────────────

def _safe_dest(root: Path, member_name: str) -> Path | None:
    """Resolve a tar member name under root, rejecting traversal / absolute paths."""
    p = (root / member_name).resolve()
    root_r = root.resolve()
    if p == root_r or root_r in p.parents:
        return p
    return None


# ── .deb payload extraction (CLI-based, no zstandard dependency) ──────────────

def _decompress(name: str, data: bytes) -> bytes:
    low = name.lower()
    if low.endswith(".tar"):
        return data
    tool = (
        ["zstd", "-dc"] if low.endswith(".zst") else
        ["xz", "-dc"] if low.endswith(".xz") else
        ["gzip", "-dc"] if low.endswith(".gz") else
        ["bzip2", "-dc"] if low.endswith(".bz2") else None
    )
    if tool is None:
        raise ValueError(f"unknown data.tar compression: {name}")
    return subprocess.run(tool, input=data, stdout=subprocess.PIPE, check=True).stdout


def _parse_ar(data: bytes) -> dict[str, bytes]:
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    entries, pos = {}, 8
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        pos += 60
        entries[name] = data[pos:pos + size]
        pos += size + (size & 1)
    return entries


def _deb_payload_tar(deb_bytes: bytes) -> tarfile.TarFile:
    ar = _parse_ar(deb_bytes)
    data_name = next((n for n in ar if n.startswith("data.tar")), None)
    if data_name is None:
        raise ValueError(f"no data.tar.* in .deb (members: {list(ar)})")
    return tarfile.open(fileobj=io.BytesIO(_decompress(data_name, ar[data_name])))


# ── tree walk ────────────────────────────────────────────────────────────────

def _walk(tf: tarfile.TarFile, dest: Path, container: str,
          manifest: list[dict], depth: int = 0) -> None:
    for m in tf.getmembers():
        target = _safe_dest(dest, m.name)
        if target is None:
            manifest.append({"path": m.name, "container": container,
                             "skipped": "unsafe path"})
            continue

        if m.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        if m.issym() or m.islnk():
            manifest.append({"path": m.name, "container": container,
                             "link_target": m.linkname,
                             "type": "symlink" if m.issym() else "hardlink"})
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
                if m.issym():
                    os.symlink(m.linkname, target)
            except OSError:
                pass
            continue

        if not m.isfile():
            continue

        data = tf.extractfile(m).read()
        sha256 = hashlib.sha256(data).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        try:
            os.chmod(target, m.mode)
        except OSError:
            pass

        manifest.append({
            "path": m.name,
            "container": container,
            "size": len(data),
            "mode": oct(m.mode),
            "mtime": int(m.mtime),
            "sha256": sha256,
            "md5": hashlib.md5(data).hexdigest(),
        })

        if DEB_RE.search(m.name) and depth < MAX_DEB_NESTING:
            sub = target.parent / (target.name + ".extracted")
            try:
                with _deb_payload_tar(data) as ntf:
                    _walk(ntf, sub, f"{container}!{m.name}" if container else m.name,
                          manifest, depth + 1)
            except Exception as exc:  # noqa: BLE001 - archival tool, keep going
                manifest.append({"path": m.name, "container": container,
                                 "deb_unpack_error": str(exc)})


# ── public API ───────────────────────────────────────────────────────────────

def extract_bundle_tree(blob: bytes, dest: Path) -> tuple[list[dict], bool]:
    """Extract `blob` (a raw signed bundle) into `dest/`. Returns (manifest, signature_valid)."""
    dest.mkdir(parents=True, exist_ok=True)
    body, sig = split_signed_bundle(blob)
    valid = verify_signature(body, sig)
    manifest: list[dict] = []
    with open_tar(body) as tf:
        _walk(tf, dest, container="", manifest=manifest)
    return manifest, valid


def esp32_bins(manifest: list[dict]) -> list[dict]:
    """Manifest entries that are an ESP32 `Seestar_x.y.z.bin` image."""
    out = []
    for e in manifest:
        if "sha256" not in e:
            continue
        m = BIN_RE.search(e["path"])
        if m:
            out.append({**e, "firmware_version": m.group(1)})
    return out


def tracked_files(dest: Path) -> list[Path]:
    """Files anywhere under `dest` whose basename is in TRACKED_BASENAMES."""
    return sorted(p for p in dest.rglob("*")
                  if p.is_file() and not p.is_symlink() and p.name in TRACKED_BASENAMES)


# zstd long-distance-matching window (2^27 = 128 MiB); helps a lot because the
# same shared libs / binaries recur all over a rootfs tree. Decompression must
# pass the same --long.
ZSTD_LONG = "27"


def make_tar_zst(src_dir: Path, out_path: Path, level: int = 19) -> None:
    """tar the *contents* of src_dir into a zstd --long compressed archive."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tar = subprocess.Popen(
        ["tar", "-C", str(src_dir), "--numeric-owner", "--sort=name", "-cf", "-", "."],
        stdout=subprocess.PIPE,
    )
    zstd = subprocess.Popen(
        ["zstd", f"-{level}", "-T0", f"--long={ZSTD_LONG}", "-q", "-f",
         "-o", str(out_path)],
        stdin=tar.stdout,
    )
    tar.stdout.close()
    if zstd.wait() != 0 or tar.wait() != 0:
        raise RuntimeError(f"tar|zstd failed for {src_dir}")


def extract_tar_zst(archive: Path, dest_dir: Path) -> None:
    """Inverse of make_tar_zst."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    zstd = subprocess.Popen(
        ["zstd", "-dc", f"--long={ZSTD_LONG}", str(archive)],
        stdout=subprocess.PIPE,
    )
    tar = subprocess.Popen(["tar", "-C", str(dest_dir), "-xf", "-"], stdin=zstd.stdout)
    zstd.stdout.close()
    if tar.wait() != 0 or zstd.wait() != 0:
        raise RuntimeError(f"zstd|tar failed for {archive}")


__all__ = [
    "TRACKED_BASENAMES", "find_firmware_bundles", "extract_bundle_tree",
    "esp32_bins", "tracked_files", "make_tar_zst", "extract_tar_zst", "BIN_RE",
]
