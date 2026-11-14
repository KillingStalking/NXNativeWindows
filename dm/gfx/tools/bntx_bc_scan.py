#!/usr/bin/env python3
"""Scan extracted Switch RomFS files for embedded BNTX textures and build a BC pack manifest.

The scanner only emits BC1/2/3/7 textures for offline ASTC conversion. BC4/5/6H
are represented as runtime-only entries. Unknown layouts are skipped, never guessed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import mmap
import os
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROMFS_MODULE = ROOT / "dm" / "tools" / "romfs.py"
MAGIC = b"BNTX\0\0\0\0"

# BNTX (channel << 8 | type) -> NVN format, symbolic format name.
BNTX_FORMATS = {
    0x1A01: (0x42, "BC1"), 0x1A06: (0x46, "BC1_SRGB"),
    0x1B01: (0x43, "BC2"), 0x1B06: (0x47, "BC2_SRGB"),
    0x1C01: (0x44, "BC3"), 0x1C06: (0x48, "BC3_SRGB"),
    0x1D01: (0x49, "BC4"), 0x1D02: (0x4A, "BC4_S"),
    0x1E01: (0x4B, "BC5"), 0x1E02: (0x4C, "BC5_S"),
    0x1F01: (0x50, "BC6H_U"), 0x1F02: (0x4F, "BC6H_S"),
    0x2001: (0x4D, "BC7"), 0x2006: (0x4E, "BC7_SRGB"),
}
RUNTIME_ONLY = {"BC4", "BC4_S", "BC5", "BC5_S", "BC6H_U", "BC6H_S"}


class BNTXError(ValueError):
    pass


def _need(buf, at: int, size: int, end: int, what: str) -> None:
    if at < 0 or size < 0 or at > end or size > end - at or at + size > len(buf):
        raise BNTXError(f"{what} is outside the BNTX file")


def _u8(buf, at, end, what):
    _need(buf, at, 1, end, what)
    return buf[at]


def _unpack(buf, fmt, at, endian, end, what):
    size = struct.calcsize(fmt)
    _need(buf, at, size, end, what)
    return struct.unpack_from(endian + fmt, buf, at)


def _name(buf, ptr, endian, end, fallback):
    if not ptr:
        return fallback
    try:
        (length,) = _unpack(buf, "H", ptr, endian, end, "texture name length")
        _need(buf, ptr + 2, length, end, "texture name")
        raw = bytes(buf[ptr + 2:ptr + 2 + length])
        return raw.decode("utf-8", errors="replace").rstrip("\0") or fallback
    except (BNTXError, struct.error):
        return fallback


def parse_bntx(buf, base=0, label="<memory>"):
    """Return validated texture metadata for one BNTX container in buf."""
    _need(buf, base, 0x58, len(buf), "BNTX header")
    if bytes(buf[base:base + 8]) != MAGIC:
        raise BNTXError(f"{label}: invalid BNTX magic")
    bom = struct.unpack_from(">H", buf, base + 0x0C)[0]
    if bom == 0xFFFE:
        endian = "<"
    elif bom == 0xFEFF:
        endian = ">"
    else:
        raise BNTXError(f"{label}: unsupported byte-order marker 0x{bom:04x}")

    (file_size,) = _unpack(buf, "I", base + 0x1C, endian, len(buf), "BNTX file size")
    if file_size < 0x58 or base + file_size > len(buf):
        raise BNTXError(f"{label}: invalid BNTX file size {file_size}")
    end = base + file_size
    platform = bytes(buf[base + 0x20:base + 0x24])
    if not platform.startswith(b"NX"):
        raise BNTXError(f"{label}: unsupported BNTX platform {platform!r}")
    (count,) = _unpack(buf, "I", base + 0x24, endian, end, "texture count")
    (table,) = _unpack(buf, "Q", base + 0x28, endian, end, "texture pointer table")
    if count > 100000:
        raise BNTXError(f"{label}: unreasonable texture count {count}")
    _need(buf, base + table, count * 8, end, "texture pointer table")

    textures = []
    for index in range(count):
        (brti,) = _unpack(buf, "Q", base + table + index * 8, endian, end, "BRTI pointer")
        brti += base
        _need(buf, brti, 0x2A0, end, "BRTI texture record")
        if bytes(buf[brti:brti + 4]) != b"BRTI":
            raise BNTXError(f"{label}: texture {index} does not point to BRTI")
        flags = _u8(buf, brti + 0x10, end, "BRTI flags")
        tile_mode, = _unpack(buf, "H", brti + 0x12, endian, end, "tile mode")
        mip_count, = _unpack(buf, "H", brti + 0x16, endian, end, "mip count")
        samples, = _unpack(buf, "H", brti + 0x18, endian, end, "sample count")
        image_format, = _unpack(buf, "I", brti + 0x1C, endian, end, "image format")
        width, = _unpack(buf, "I", brti + 0x24, endian, end, "texture width")
        height, = _unpack(buf, "I", brti + 0x28, endian, end, "texture height")
        depth, = _unpack(buf, "I", brti + 0x2C, endian, end, "texture depth")
        array_length, = _unpack(buf, "I", brti + 0x30, endian, end, "array length")
        layout_raw, = _unpack(buf, "Q", brti + 0x34, endian, end, "packaged layout")
        image_size, = _unpack(buf, "I", brti + 0x50, endian, end, "image size")
        channel_mapping = list(bytes(buf[brti + 0x58:brti + 0x5C]))
        dimension = _u8(buf, brti + 0x5C, end, "image dimension")
        name_ptr, = _unpack(buf, "Q", brti + 0x60, endian, end, "texture name pointer")
        mip_table, = _unpack(buf, "Q", brti + 0x70, endian, end, "mipmap pointer table")
        mip_count = max(1, mip_count)
        _need(buf, mip_table + base, mip_count * 8, end, "mipmap pointer table")
        mip_ptrs = [
            _unpack(buf, "Q", base + mip_table + mip * 8, endian, end, "mipmap data pointer")[0]
            for mip in range(mip_count)
        ]
        if not mip_ptrs or any(p == 0 or p >= file_size for p in mip_ptrs):
            raise BNTXError(f"{label}: texture {index} has invalid mip data pointers")
        if any(a > b for a, b in zip(mip_ptrs, mip_ptrs[1:])):
            raise BNTXError(f"{label}: texture {index} mip pointers are not monotonic")
        data_start = base + mip_ptrs[0]
        _need(buf, data_start, image_size, end, "texture image allocation")
        if any(p < mip_ptrs[0] or p >= mip_ptrs[0] + image_size for p in mip_ptrs):
            raise BNTXError(f"{label}: texture {index} mip pointer is outside its image allocation")
        tex_name = _name(buf, base + name_ptr if name_ptr else 0, endian, end, f"texture_{index}")
        fmt = BNTX_FORMATS.get(image_format)
        if fmt is None:
            continue
        nvn_format, format_name = fmt

        # BNTX ImageDimension -> NVN texture target. 3D/MS remain on runtime path.
        if dimension == 0:
            target, nvn_height, nvn_depth, layers = 0, 1, 1, 1
        elif dimension == 1:
            target, nvn_height, nvn_depth, layers = 1, max(1, height), 1, 1
        elif dimension == 2:
            target, nvn_height, nvn_depth, layers = 2, max(1, height), max(1, depth), 1
        elif dimension == 3:
            target, nvn_height, nvn_depth, layers = 8, max(1, height), 1, 6
        elif dimension == 4:
            layers = max(1, array_length)
            target, nvn_height, nvn_depth = 3, layers, 1
        elif dimension == 5:
            layers = max(1, array_length)
            target, nvn_height, nvn_depth = 4, max(1, height), layers
        elif dimension == 8:
            layers = max(1, array_length) * 6
            target, nvn_height, nvn_depth = 9, max(1, height), layers
        else:
            target, nvn_height, nvn_depth, layers = -1, max(1, height), max(1, depth), 1

        # BNTX flags: bit 0 means packaged layout is valid; bits 1/2 indicate sparse modes.
        pkg_layout = 0
        if flags & 1:
            layout_b0 = layout_raw & 0xFF
            layout_b1 = (layout_raw >> 8) & 0xFF
            encoded = layout_b0 | ((layout_b1 & 0x0F) << 8)
            pkg_layout = encoded + 1 if encoded else 0
        nvn = {
            "target": target, "format": f"0x{nvn_format:02x}",
            "flags": 0, "samples": max(1, samples),
            "width": max(1, width), "height": nvn_height,
            "depth": nvn_depth, "levels": mip_count, "stride": 0,
            "pkg_layout": pkg_layout, "switch2": False,
        }
        textures.append({
            "index": index, "name": tex_name, "format": format_name,
            "nvn_format": nvn_format, "dimension": dimension,
            "tile_mode": tile_mode, "flags": flags, "samples": samples,
            "width": width, "height": height, "depth": depth,
            "array_length": array_length, "layers": layers,
            "mip_offsets": [p - mip_ptrs[0] for p in mip_ptrs],
            "data_offset": data_start, "image_size": image_size,
            "nvn": nvn, "channel_mapping": channel_mapping,
            "runtime_only": format_name in RUNTIME_ONLY,
            "eligible": (dimension in (0, 1, 3, 4, 5, 8)
                         and tile_mode == 0 and not (flags & 0x06) and samples <= 1),
        })
    return {"base": base, "size": file_size, "textures": textures}



def decompress_yaz0(buf, base=0, max_output=1024 * 1024 * 1024):
    """Decode a Yaz0 stream with strict input/output bounds."""
    if bytes(buf[base:base + 4]) != b"Yaz0" or base + 16 > len(buf):
        raise BNTXError("invalid Yaz0 header")
    (size,) = struct.unpack_from(">I", buf, base + 4)
    if not size or size > max_output:
        raise BNTXError(f"Yaz0 output size {size} is outside the allowed range")
    src = base + 16
    out = bytearray(size)
    dst = 0
    while dst < size:
        if src >= len(buf):
            raise BNTXError("truncated Yaz0 code stream")
        code = buf[src]
        src += 1
        for bit in range(7, -1, -1):
            if dst >= size:
                break
            if code & (1 << bit):
                if src >= len(buf):
                    raise BNTXError("truncated Yaz0 literal")
                out[dst] = buf[src]
                src += 1
                dst += 1
            else:
                if src + 2 > len(buf):
                    raise BNTXError("truncated Yaz0 back-reference")
                b1, b2 = buf[src], buf[src + 1]
                src += 2
                distance = ((b1 & 0x0F) << 8) | b2
                length = b1 >> 4
                if length == 0:
                    if src >= len(buf):
                        raise BNTXError("truncated Yaz0 extended length")
                    length = buf[src] + 0x12
                    src += 1
                else:
                    length += 2
                copy_from = dst - distance - 1
                if copy_from < 0 or length > size - dst:
                    raise BNTXError("invalid Yaz0 back-reference range")
                for _ in range(length):
                    out[dst] = out[copy_from]
                    dst += 1
                    copy_from += 1
    return bytes(out)


def iter_bntx_containers(buf, label, errors, depth=0):
    """Find direct or Yaz0-compressed BNTX containers, including embedded SARC entries."""
    pos = 0
    while True:
        pos = buf.find(b"BNTX", pos)
        if pos < 0:
            break
        if bytes(buf[pos:pos + 8]) != MAGIC:
            pos += 4
            continue
        try:
            parsed = parse_bntx(buf, pos, label)
            yield buf, pos, label, parsed
            pos += parsed["size"]
        except (BNTXError, struct.error) as exc:
            errors.append(f"{label}@0x{pos:x}: {exc}")
            pos += 4

    if depth >= 4:
        return
    pos = 0
    while True:
        pos = buf.find(b"Yaz0", pos)
        if pos < 0:
            break
        try:
            decoded = decompress_yaz0(buf, pos)
            yield from iter_bntx_containers(decoded, f"{label}#Yaz0@0x{pos:x}", errors, depth + 1)
        except (BNTXError, struct.error) as exc:
            errors.append(f"{label}#Yaz0@0x{pos:x}: {exc}")
        pos += 4

def _load_romfs():
    spec = importlib.util.spec_from_file_location("dm_romfs_scan", ROMFS_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or "texture"


def _files(root, fs):
    p = fs.Path(root)
    if p.is_file():
        yield str(p)
        return
    for directory, dirs, files in fs.os.walk(str(p)):
        dirs.sort()
        for filename in sorted(files):
            yield fs.os.path.join(directory, filename)


def scan(root, out_dir):
    fs = _load_romfs()
    root_path = fs.Path(root)
    if root_path.is_dir():
        candidate = root_path / "RomFS.img"
        if fs.is_image(candidate):
            root = str(candidate)
    out_dir = Path(out_dir).resolve()
    source_dir = out_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries = []
    containers = 0
    textures_seen = 0
    skipped_layouts = 0
    errors = []

    for file_path in _files(root, fs):
        try:
            with fs.open(file_path, "rb") as stream:
                if not stream.seekable():
                    continue
                stream.seek(0, os.SEEK_END)
                file_len = stream.tell()
                if file_len < 8:
                    continue
                stream.seek(0)
                mapped = fs.mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    for blob, base, blob_label, parsed in iter_bntx_containers(mapped, file_path, errors):
                        containers += 1
                        for tex in parsed["textures"]:
                            textures_seen += 1
                            if not tex["eligible"]:
                                skipped_layouts += 1
                                continue
                            stem = _safe(f"{Path(blob_label).name}_{base:x}_{tex['index']}_{tex['name']}")
                            common = {
                                "mip_offsets": tex["mip_offsets"],
                                "nvn": tex["nvn"],
                                "channel_mapping": tex["channel_mapping"],
                            }
                            if not tex["runtime_only"]:
                                source_name = f"{stem}.bin"
                                source_path = source_dir / source_name
                                start = tex["data_offset"]
                                source_path.write_bytes(bytes(blob[start:start + tex["image_size"]]))
                                common.update({
                                    "source": f"sources/{source_name}",
                                    "offset": 0,
                                    "storage_size": tex["image_size"],
                                })
                            for mip in range(tex["nvn"]["levels"]):
                                for layer in range(tex["layers"]):
                                    ident = f"{stem}_m{mip}_l{layer}"
                                    manifest_entries.append({
                                        "id": ident, "mip": mip, "layer": layer,
                                        **common,
                                    })
                finally:
                    mapped.close()
        except (OSError, ValueError, BufferError) as exc:
            errors.append(f"{file_path}: {exc}")

    manifest = {"version": 1, "textures": manifest_entries}
    manifest_path = out_dir / "bc_offline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    summary = {
        "containers": containers,
        "recognized_bc_textures": textures_seen,
        "manifest_entries": len(manifest_entries),
        "skipped_layouts_or_dimensions": skipped_layouts,
        "scan_errors": errors,
        "manifest": manifest_path.name,
    }
    (out_dir / "bntx_scan_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="extracted RomFS directory, RomFS.img path, or one container file")
    parser.add_argument("--out", required=True, type=Path, help="output folder for extracted BC sources and manifest")
    args = parser.parse_args()
    summary = scan(args.root, args.out)
    print(json.dumps(summary, indent=2))
    return 0 if summary["containers"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, BNTXError, struct.error) as exc:
        print(f"[BNTX-BC-SCAN] FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
