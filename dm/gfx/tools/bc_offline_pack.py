#!/usr/bin/env python3
"""Build a sidecar ASTC 4x4 pack from explicit NVN or linear BC subresource manifests.

NVN entries are read from complete block-linear texture storage and deswizzled with
this repository's nvn_tex / nvn_deswizzle implementation. Linear entries are also
accepted for isolated tests. BC4/BC5/BC6H stay on the runtime decoder.
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
TOOLS = ROOT / "dm" / "gfx" / "tools"
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
NVN_FORMATS = {
    0x41: "BC1", 0x42: "BC1", 0x43: "BC2", 0x44: "BC3",
    0x45: "BC1_SRGB", 0x46: "BC1_SRGB", 0x47: "BC2_SRGB", 0x48: "BC3_SRGB",
    0x49: "BC4", 0x4A: "BC4_S", 0x4B: "BC5", 0x4C: "BC5_S",
    0x4D: "BC7", 0x4E: "BC7_SRGB", 0x4F: "BC6H_S", 0x50: "BC6H_U",
}
RUNTIME_ONLY = {"BC4", "BC4_S", "BC5", "BC5_S", "BC6H", "BC6H_S", "BC6H_U"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not name:
        raise ValueError("texture id must contain at least one safe filename character")
    return name


def build_decoder(clang: str, temp: Path) -> Path:
    if os.name == "nt":
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
            "-fno-builtin", "-fno-stack-protector", "-nostdlib", "-shared",
            "-fuse-ld=lld", "-Wl,/NOENTRY",
            "-Wl,/EXPORT:bc_offline_texture",
            "-Wl,/EXPORT:bc_offline_texture_level",
            "-Wl,/EXPORT:bc_offline_level_offset", "-Wl,/EXPORT:bc_offline_texture_key",
            f"-I{TEX}", f"-I{ROOT / 'dm' / 'gfx' / 'nvn'}", "-o", str(dll),
            str(TEX / "bc_soft.c"), str(TEX / "nvn_bc2astc.c"),
            str(TEX / "nvn_deswizzle.c"), str(TEX / "nvn_fmt_mtl.c"),
            str(ROOT / "dm" / "gfx" / "nvn" / "nvn_tex.c"),
            str(TOOLS / "bc_offline.c"), str(crt),
        ]
        output = dll
    else:
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        output = temp / ("bc_offline" + suffix)
        linker = "-dynamiclib" if sys.platform == "darwin" else "-shared"
        cmd = [
            clang, "-O2", "-fPIC", linker, "-fno-strict-aliasing",
            f"-I{TEX}", f"-I{ROOT / 'dm' / 'gfx' / 'nvn'}", "-o", str(output),
            str(TEX / "bc_soft.c"), str(TEX / "nvn_bc2astc.c"),
            str(TEX / "nvn_deswizzle.c"), str(TEX / "nvn_fmt_mtl.c"),
            str(ROOT / "dm" / "gfx" / "nvn" / "nvn_tex.c"),
            str(TOOLS / "bc_offline.c"), "-lm",
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("could not build BC offline decoder:\n" + result.stderr[-6000:])
    return output

def load_decoder(path: Path):
    lib = ctypes.CDLL(str(path))
    fn = lib.bc_offline_texture
    fn.argtypes = [
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
    ]
    fn.restype = ctypes.c_int
    level = lib.bc_offline_texture_level
    level.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t),
    ]
    level.restype = ctypes.c_int
    fn.level = level
    level_offset = lib.bc_offline_level_offset
    level_offset.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32,
    ]
    level_offset.restype = ctypes.c_uint64
    fn.level_offset = level_offset
    texture_key = lib.bc_offline_texture_key
    texture_key.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]
    texture_key.restype = ctypes.c_uint64
    fn.texture_key = texture_key
    fn._bc_library = lib
    return fn

def _as_int(value) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def convert_entry(entry: dict, base: Path, out_dir: Path, decode) -> dict:
    if "id" not in entry:
        raise ValueError("entry missing field: id")
    mip = _as_int(entry.get("mip", 0))
    layer = _as_int(entry.get("layer", 0))
    if mip < 0 or layer < 0:
        raise ValueError(f"{entry['id']}: mip and layer must be non-negative")
    if "nvn" in entry:
        nvn_hint = entry["nvn"]
        hinted_format = NVN_FORMATS.get(_as_int(nvn_hint.get("format", -1)))
    else:
        hinted_format = str(entry.get("format", "")).upper()
    if hinted_format in RUNTIME_ONLY:
        return {
            "id": str(entry["id"]), "format": hinted_format,
            "mip": mip, "layer": layer, "skipped": True,
            "reason": "kept on the runtime BC decoder",
        }
    if "source" not in entry:
        raise ValueError(f"{entry['id']}: entry missing field: source")
    offset = _as_int(entry.get("offset", 0))
    if offset < 0:
        raise ValueError(f"{entry['id']}: negative source offset")
    source = (base / entry["source"]).resolve()
    with source.open("rb") as f:
        f.seek(offset)
        storage_size = _as_int(entry["storage_size"]) if "storage_size" in entry else source.stat().st_size - offset
        if storage_size < 0:
            raise ValueError(f"{entry['id']}: invalid storage_size")
        raw = f.read(storage_size)
    if len(raw) != storage_size:
        raise ValueError(f"{entry['id']}: source storage is truncated (need {storage_size} bytes, got {len(raw)})")

    if "nvn" in entry:
        nvn = entry["nvn"]
        for key in ("target", "format", "width", "height"):
            if key not in nvn:
                raise ValueError(f"{entry['id']}: nvn descriptor missing {key}")
        target = _as_int(nvn["target"])
        nvn_format = _as_int(nvn["format"])
        fmt = NVN_FORMATS.get(nvn_format)
        if fmt is None:
            raise ValueError(f"{entry['id']}: NVN format 0x{nvn_format:x} is not BC1/BC2/BC3/BC7")
        if "format" in entry and str(entry["format"]).upper() != fmt:
            raise ValueError(f"{entry['id']}: format label does not match NVN format 0x{nvn_format:x} ({fmt})")
        pf, block_bytes, astc_format = FORMATS[fmt]
        width, height = _as_int(nvn["width"]), _as_int(nvn["height"])
        depth = _as_int(nvn.get("depth", 1))
        levels = _as_int(nvn.get("levels", 1))
        flags = _as_int(nvn.get("flags", 0))
        samples = _as_int(nvn.get("samples", 1))
        stride = _as_int(nvn.get("stride", 0))
        pkg_layout = _as_int(nvn.get("pkg_layout", 0))
        switch2 = bool(nvn.get("switch2", False))
        if width <= 0 or height <= 0 or levels <= 0 or mip >= levels:
            raise ValueError(f"{entry['id']}: invalid dimensions, mip count, or mip index")
        if "mip_offsets" in entry:
            offsets = [_as_int(x) for x in entry["mip_offsets"]]
            if len(offsets) != levels or any(x < 0 for x in offsets):
                raise ValueError(f"{entry['id']}: mip_offsets must contain one non-negative offset per mip")
            expected_offsets = [
                int(decode.level_offset(target, nvn_format, flags, samples, width, height,
                                        depth, levels, stride, pkg_layout, i))
                for i in range(levels)
            ]
            if offsets != expected_offsets:
                raise ValueError(
                    f"{entry['id']}: BNTX mip pointer offsets {offsets} do not match "
                    f"the repository NVN layout {expected_offsets}; refusing unsafe conversion"
                )
        w = max(1, width >> mip)
        h = 1 if target in (0, 3) else max(1, height >> mip)
        blocks_x, blocks_y = (w + 3) // 4, (h + 3) // 4
        scratch_pitch = blocks_x * block_bytes
        scratch_size = scratch_pitch * blocks_y
        dst_pitch_expected = blocks_x * 16
        dst_size_expected = dst_pitch_expected * blocks_y
        src_buf = ctypes.create_string_buffer(raw, len(raw))
        scratch_buf = ctypes.create_string_buffer(scratch_size)
        dst_buf = ctypes.create_string_buffer(dst_size_expected)
        dst_pitch = ctypes.c_size_t()
        dst_size = ctypes.c_size_t()
        rc = decode.level(
            target, nvn_format, flags, samples,
            width, height, depth, levels, stride, pkg_layout, mip, layer, int(switch2),
            src_buf, len(raw), scratch_buf, scratch_size,
            dst_buf, dst_size_expected, ctypes.byref(dst_pitch), ctypes.byref(dst_size),
        )
        if rc:
            raise RuntimeError(f"{entry['id']}: NVN deswizzle/transcoder returned {rc}")
        if dst_pitch.value != dst_pitch_expected or dst_size.value != dst_size_expected:
            raise RuntimeError(f"{entry['id']}: unexpected ASTC layout from native transcoder")
        converted = dst_buf.raw[:dst_size.value]
        key = int(decode.texture_key(
            target, nvn_format, flags, samples, width, height, depth, levels,
            stride, pkg_layout, src_buf, len(raw),
        ))
        if key == 0xFFFFFFFFFFFFFFFF:
            raise ValueError(f"{entry['id']}: NVN layout size exceeds the supplied source allocation")
        metadata = {
            "texture_key": f"{key:016x}",
            "nvn_target": target,
            "nvn_format": f"0x{nvn_format:02x}",
            "nvn_flags": flags,
            "nvn_samples": samples,
            "nvn_pkg_layout": pkg_layout,
            "switch2_layout": switch2,
            "mip_offsets_validated": "mip_offsets" in entry,
        }
    else:
        if "format" not in entry or "width" not in entry or "height" not in entry:
            raise ValueError(f"{entry['id']}: linear input requires format, width, and height")
        fmt = str(entry["format"]).upper()
        if fmt not in FORMATS:
            if fmt in RUNTIME_ONLY:
                return {
                    "id": str(entry["id"]), "format": fmt,
                    "mip": mip, "layer": layer, "skipped": True,
                    "reason": "kept on the runtime BC decoder",
                }
            raise ValueError(f"{entry['id']}: unsupported format {fmt}")
        pf, block_bytes, astc_format = FORMATS[fmt]
        width, height = int(entry["width"]), int(entry["height"])
        if width <= 0 or height <= 0:
            raise ValueError(f"{entry['id']}: dimensions must be positive")
        blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
        min_pitch = blocks_x * block_bytes
        src_pitch = int(entry.get("row_pitch", min_pitch))
        if src_pitch < min_pitch:
            raise ValueError(f"{entry['id']}: row_pitch {src_pitch} is smaller than {min_pitch}")
        expected = (blocks_y - 1) * src_pitch + min_pitch
        if len(raw) < expected:
            raise ValueError(f"{entry['id']}: linear source is truncated (need {expected} bytes, got {len(raw)})")
        dst_pitch = blocks_x * 16
        dst_size = dst_pitch * blocks_y
        src_buf = ctypes.create_string_buffer(raw, len(raw))
        dst_buf = ctypes.create_string_buffer(dst_size)
        rc = decode(pf, src_buf, src_pitch, width, height, dst_buf, dst_pitch)
        if rc:
            raise RuntimeError(f"{entry['id']}: transcoder returned {rc}")
        converted = dst_buf.raw[:dst_size]
        astc_format = FORMATS[fmt][2]
        metadata = {"linear_input": True}

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
        "width": width if "nvn" not in entry else max(1, width >> mip),
        "height": height if "nvn" not in entry else (1 if int(entry["nvn"]["target"]) in (0, 3) else max(1, height >> mip)),
        "mip": mip,
        "layer": layer,
        "output": out_name,
        "row_pitch": int(dst_pitch.value) if "nvn" in entry else int(dst_pitch),
        "size": len(converted),
        "source_sha256": sha256(raw),
        "output_sha256": sha256(converted),
        **metadata,
        **({"channel_mapping": entry["channel_mapping"]} if "channel_mapping" in entry else {}),
    }


def bundle_results(results: list[dict], out_dir: Path) -> Path:
    """Concatenate converted subresources into one seekable sidecar blob."""
    pack_path = out_dir / "bc_offline_pack.bin"
    tmp_path = pack_path.with_suffix(".bin.tmp")
    try:
        with tmp_path.open("wb") as pack:
            for item in results:
                if item.get("skipped", False):
                    continue
                source = out_dir / item["output"]
                data = source.read_bytes()
                if len(data) != item["size"] or sha256(data) != item["output_sha256"]:
                    raise ValueError(f"{item['id']}: ASTC output changed before pack assembly")
                offset = pack.tell()
                pack.write(data)
                item["pack_offset"] = offset
                item["pack_size"] = len(data)
                item["pack_file"] = pack_path.name
                source.unlink()
        os.replace(tmp_path, pack_path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return pack_path

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path, help="JSON manifest of NVN block-linear or linear BC subresources")
    ap.add_argument("--out", required=True, type=Path, help="sidecar pack output directory")
    ap.add_argument("--clang", default=os.environ.get("CLANG") or shutil.which("clang"), help="clang executable")
    ap.add_argument("--decoder-dll", type=Path, help="use an already-built bc_offline.dll")
    args = ap.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("textures"), list):
        raise ValueError("manifest must contain version=1 and a textures array")
    ids = [str(e.get("id", "")) for e in manifest["textures"]]
    if len(ids) != len(set(ids)):
        raise ValueError("texture ids must be unique (use a distinct id for every mip/layer)")
    names = [safe_name(x) for x in ids]
    if len(names) != len(set(names)):
        raise ValueError("texture ids collide after filename sanitization; choose distinct ids")
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
    pack_path = bundle_results(results, out_dir)
    index = {
        "version": 1,
        "texture_format": "ASTC_4x4",
        "pack_file": pack_path.name,
        "source_manifest": manifest_path.name,
        "textures": results,
    }
    index_path = out_dir / "bc_offline_pack.json"
    tmp_index = index_path.with_suffix(".json.tmp")
    tmp_index.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_index, index_path)
    converted_count = sum(not item.get("skipped", False) for item in results)
    skipped_count = len(results) - converted_count
    print(f"converted {converted_count} BC texture subresources; kept {skipped_count} on runtime decoder -> {out_dir}")
    print(f"index: {index_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[BC-OFFLINE] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
