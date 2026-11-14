#!/usr/bin/env python3
"""Scan a Switch RomFS for BNTX BC textures and build a runtime ASTC sidecar pack."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
SCANNER = TOOLS / "bntx_bc_scan.py"
PACKER = TOOLS / "bc_offline_pack.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="extracted RomFS directory, RomFS.img, or one container file")
    parser.add_argument("--out", required=True, type=Path, help="output directory (contains scan/ and bc_offline_pack/)")
    parser.add_argument("--clang", help="clang executable used to build the native converter")
    parser.add_argument("--decoder-dll", help="reuse an existing compatible offline decoder DLL/shared library")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        parser.error(f"RomFS input does not exist: {root}")
    out = args.out.resolve()
    scan_out = out / "scan"
    pack_out = out / "bc_offline_pack"
    for path in (scan_out, pack_out):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    scan_cmd = [sys.executable, str(SCANNER), "--root", str(root), "--out", str(scan_out)]
    print("[BC-OFFLINE] scanning BNTX containers...")
    scan = subprocess.run(scan_cmd, check=False)
    if scan.returncode:
        raise RuntimeError(f"BNTX scan failed with exit code {scan.returncode}")
    manifest = scan_out / "bc_offline_manifest.json"
    if not manifest.is_file():
        raise RuntimeError("scanner did not create bc_offline_manifest.json")

    pack_cmd = [
        sys.executable, str(PACKER),
        "--manifest", str(manifest),
        "--out", str(pack_out),
    ]
    if args.clang:
        pack_cmd += ["--clang", args.clang]
    if args.decoder_dll:
        pack_cmd += ["--decoder-dll", args.decoder_dll]
    print("[BC-OFFLINE] converting eligible BC1/BC2/BC3/BC7 textures...")
    subprocess.run(pack_cmd, check=True)

    print("[BC-OFFLINE] complete")
    print(f"  scan report: {scan_out / 'bntx_scan_summary.json'}")
    print(f"  runtime pack: {pack_out}")
    print("  install folder: copy bc_offline_pack/ into the game's split-data folder or app resources")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[BC-OFFLINE] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
