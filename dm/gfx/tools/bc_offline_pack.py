#!/usr/bin/env python3
"""Build a sidecar ASTC 4x4 pack from an explicit manifest of linear BC subresources.

The input files are never modified. Each manifest entry describes one mip/layer
whose BC blocks have already been deswizzled into linear block-row order.
BC4/BC5/BC6H are deliberately not accepted; they stay on the runtime decoder.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEX = ROOT / "dm" / "gfx" / "tex"
FORMATS = {
    "BC1": (130, 8, "ASTC_4x4_LDR"),
    "BC1_SRGB": (131, 8, "ASTC_4x4_SRGB"),
    "BC2": (132, 16, "ASTC_4x4_LDR"),
    "BC2_SRGB": (133, 16, "ASTC_4x4_SRGB"),
    "BC3": (134, 16, "ASTC_4x4_LDR"),
    "BC3_SRGB": (135, 16, "ASTC_4x4_SRGB"),
    "BC7": (152, 16, "ASTC_4x4_LDR"),
    "BC7_SRGB": (153, 16, "ASTC_4x4_SRGB"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not name:
        raise ValueError("texture id must contain at least one safe filename character")
    return name


def build_decoder(clang: str, temp: Path) -> Path:
    crt = temp / "bc_offline_crt.c"
    crt.write_text(
        "#include <stddef.h>\n"
        "void *memcpy(void *d,const void *s,size_t n){unsigned char *a=d;const unsigned char *b=s;while(n--)*a++=*b++;return d;}\n"
        "void *memset(void *d,int c,size_t n){unsigned char *a=d;while(n--)*a++=(unsigned char)c;return d;}\n"
        "int _fltused;\n",
        encoding="ascii",
    )
    dll = temp / "bc_offline.dll"
    cmd = [
        clang, "--target=x86_64-pc-windows-msvc", "-O2", "-ffreestanding",
        "-fno-builtin", "-nostdlib", "-shared", "-fuse-ld=lld",
        "-Wl,/NOENTRY", "-Wl,/EXPORT:bc_offline_texture",
        f"-I{TEX}", "-o", str(dll),
        str(TEX / "bc_soft.c"), str(TEX / "nvn_bc2astc.c"),
        str(TEX / "bc_offline.c"), str(crt),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("could not build BC offline decoder:\n" + result.stderr[-6000:])
    return dll


def load_decoder(path: Path):
    lib = ctypes.CDLL(str(path))
    fn = lib.bc_offline_texture
    fn.argtypes = [
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
    ]
    fn.restype = ctypes.c_int
    return fn


def convert_entry(entry: dict, base: Path, out_dir: Path, decode) -> dict:
    required = ("id", "format", "width", "height", "source")
    missing = [key for key in required if key not in entry]
    if missing:
        raise ValueError(f"entry missing fields: {', '.join(missing)}")
    fmt = str(entry["format"]).upper()
    if fmt not in FORMATS:
        if fmt in ("BC4", "BC4_SRGB", "BC5", "BC5_SRGB", "BC6H", "BC6H_SRGB"):
            raise ValueError(f"{entry['id']}: {fmt} is intentionally left to the runtime decoder")
        raise ValueError(f"{entry['id']}: unsupported format {fmt}")
    pf, block_bytes, astc_format = FORMATS[fmt]
    width, height = int(entry["width"]), int(entry["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"{entry['id']}: dimensions must be positive")
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    min_pitch = blocks_x * block_bytes
    src_pitch = int(entry.get("row_pitch", min_pitch))
    dst_pitch = blocks_x * 16
    if src_pitch < min_pitch:
        raise ValueError(f"{entry['id']}: row_pitch {src_pitch} is smaller than {min_pitch}")
    offset = int(entry.get("offset", 0))
    source = (base / entry["source"]).resolve()
    if offset < 0:
        raise ValueError(f"{entry['id']}: negative source offset")
    with source.open("rb") as f:
        f.seek(offset)
        raw = f.read((blocks_y - 1) * src_pitch + min_pitch)
    expected = (blocks_y - 1) * src_pitch + min_pitch
    if len(raw) != expected:
        raise ValueError(f"{entry['id']}: source is truncated (need {expected} bytes, got {len(raw)})")
    dst_size = dst_pitch * blocks_y
    src_buf = ctypes.create_string_buffer(raw, len(raw))
    dst_buf = ctypes.create_string_buffer(dst_size)
    rc = decode(pf, src_buf, src_pitch, width, height, dst_buf, dst_pitch)
    if rc:
        raise RuntimeError(f"{entry['id']}: transcoder returned {rc}")
    converted = dst_buf.raw[:dst_size]
    out_name = safe_name(str(entry["id"])) + ".astc"
    out_path = out_dir / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(converted)
    os.replace(tmp_path, out_path)
    return {
        "id": str(entry["id"]),
        "format": fmt,
        "astc_format": astc_format,
        "width": width,
        "height": height,
        "mip": int(entry.get("mip", 0)),
        "layer": int(entry.get("layer", 0)),
        "output": out_name,
        "row_pitch": dst_pitch,
        "size": dst_size,
        "source_sha256": sha256(raw),
        "output_sha256": sha256(converted),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path, help="JSON manifest of linear BC subresources")
    ap.add_argument("--out", required=True, type=Path, help="sidecar pack output directory")
    ap.add_argument("--clang", default=os.environ.get("CLANG") or shutil.which("clang"), help="Windows clang executable")
    ap.add_argument("--decoder-dll", type=Path, help="use an already-built bc_offline.dll")
    args = ap.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("textures"), list):
        raise ValueError("manifest must contain version=1 and a textures array")
    ids = [str(e.get("id", "")) for e in manifest["textures"]]
    if len(ids) != len(set(ids)):
        raise ValueError("texture ids must be unique (use a distinct id for every mip/layer)")
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.decoder_dll:
        decoder_path = args.decoder_dll.resolve()
        if not decoder_path.is_file():
            raise FileNotFoundError(decoder_path)
        decode = load_decoder(decoder_path)
        results = [convert_entry(e, manifest_path.parent, out_dir, decode) for e in manifest["textures"]]
    else:
        if not args.clang:
            raise RuntimeError("clang was not found; pass --clang or set CLANG")
        with tempfile.TemporaryDirectory(prefix="dm-bc-offline-") as td:
            decoder_path = build_decoder(args.clang, Path(td))
            decode = load_decoder(decoder_path)
            results = [convert_entry(e, manifest_path.parent, out_dir, decode) for e in manifest["textures"]]
    index = {
        "version": 1,
        "texture_format": "ASTC_4x4",
        "source_manifest": manifest_path.name,
        "textures": results,
    }
    index_path = out_dir / "bc_offline_pack.json"
    tmp_index = index_path.with_suffix(".json.tmp")
    tmp_index.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_index, index_path)
    print(f"converted {len(results)} BC texture subresources -> {out_dir}")
    print(f"index: {index_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[BC-OFFLINE] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
