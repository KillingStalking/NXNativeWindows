#!/usr/bin/env python3
"""Synthetic BNTX parser tests; no game assets are required."""
import importlib.util
import struct
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "bntx_bc_scan.py"
spec = importlib.util.spec_from_file_location("bntx_bc_scan", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load BNTX scanner at {SCRIPT}")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def make_bntx(image_format=0x1C01, dimension=1, tile_mode=0, flags=0, width=4, height=4):
    data = bytearray(0x800)
    data[0:8] = b"BNTX\0\0\0\0"
    struct.pack_into("<I", data, 0x08, 0x00040000)
    struct.pack_into("<H", data, 0x0C, 0xFFFE)
    struct.pack_into("<I", data, 0x1C, len(data))
    data[0x20:0x24] = b"NX  "
    struct.pack_into("<I", data, 0x24, 1)
    struct.pack_into("<Q", data, 0x28, 0x198)
    struct.pack_into("<Q", data, 0x198, 0x200)
    data[0x200:0x204] = b"BRTI"
    struct.pack_into("<I", data, 0x204, 0x2A8)
    data[0x210] = flags
    data[0x211] = 2
    struct.pack_into("<H", data, 0x212, tile_mode)
    struct.pack_into("<H", data, 0x216, 1)
    struct.pack_into("<H", data, 0x218, 1)
    struct.pack_into("<I", data, 0x21C, image_format)
    struct.pack_into("<I", data, 0x224, width)
    struct.pack_into("<I", data, 0x228, height)
    struct.pack_into("<I", data, 0x22C, 1)
    struct.pack_into("<I", data, 0x230, 1)
    struct.pack_into("<Q", data, 0x234, 0)
    struct.pack_into("<I", data, 0x250, 512)
    struct.pack_into("<I", data, 0x254, 512)
    data[0x25C] = dimension
    struct.pack_into("<Q", data, 0x260, 0x500)
    struct.pack_into("<Q", data, 0x270, 0x520)
    struct.pack_into("<H", data, 0x500, 3)
    data[0x502:0x505] = b"tex"
    struct.pack_into("<Q", data, 0x520, 0x600)
    data[0x5F0:0x5F4] = b"BRTD"
    data[0x600:0x800] = bytes(range(256)) * 2
    return bytes(data)


class BNTXScanner(unittest.TestCase):
    def test_reads_bntx_texture_and_nvn_descriptor(self):
        parsed = scan.parse_bntx(make_bntx(), 0, "fixture.bntx")
        self.assertEqual(parsed["size"], 0x800)
        self.assertEqual(len(parsed["textures"]), 1)
        tex = parsed["textures"][0]
        self.assertEqual(tex["name"], "tex")
        self.assertEqual(tex["format"], "BC3")
        self.assertEqual(tex["nvn"]["target"], 1)
        self.assertEqual(tex["nvn"]["format"], "0x44")
        self.assertEqual(tex["mip_offsets"], [0])
        self.assertTrue(tex["eligible"])

    def test_parses_embedded_bntx_with_container_relative_offsets(self):
        wrapped = b"prefix" + make_bntx() + b"suffix"
        parsed = scan.parse_bntx(wrapped, 6, "wrapper.bin")
        self.assertEqual(parsed["base"], 6)
        self.assertEqual(parsed["textures"][0]["data_offset"], 6 + 0x600)

    def test_bc4_bc5_bc6h_are_runtime_only(self):
        cases = [(0x1D01, "BC4"), (0x1E02, "BC5_S"), (0x1F01, "BC6H_U")]
        for image_format, name in cases:
            with self.subTest(format=name):
                tex = scan.parse_bntx(make_bntx(image_format), 0)["textures"][0]
                self.assertEqual(tex["format"], name)
                self.assertTrue(tex["runtime_only"])
                self.assertTrue(tex["eligible"])

    def test_linear_and_sparse_textures_are_not_marked_eligible(self):
        linear = scan.parse_bntx(make_bntx(tile_mode=1), 0)["textures"][0]
        sparse = scan.parse_bntx(make_bntx(flags=0x02), 0)["textures"][0]
        self.assertFalse(linear["eligible"])
        self.assertFalse(sparse["eligible"])

    def test_yaz0_literal_stream_and_embedded_bntx(self):
        raw = make_bntx()
        encoded = bytearray(b"Yaz0" + struct.pack(">I", len(raw)) + bytes(8))
        for pos in range(0, len(raw), 8):
            chunk = raw[pos:pos + 8]
            encoded.append((0xFF << (8 - len(chunk))) & 0xFF)
            encoded.extend(chunk)
        self.assertEqual(scan.decompress_yaz0(encoded), raw)
        errors = []
        found = list(scan.iter_bntx_containers(encoded, "fixture.szs", errors))
        self.assertFalse(errors)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][3]["textures"][0]["format"], "BC3")

    def test_scan_writes_manifest_and_extracts_texture_storage(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "RomFS"
            out = Path(td) / "offline"
            root.mkdir()
            (root / "fixture.bntx").write_bytes(make_bntx())
            summary = scan.scan(str(root), str(out))
            self.assertEqual(summary["containers"], 1)
            manifest = json.loads((out / "bc_offline_manifest.json").read_text())
            self.assertEqual(len(manifest["textures"]), 1)
            entry = manifest["textures"][0]
            self.assertEqual(entry["nvn"]["format"], "0x44")
            source = out / entry["source"]
            self.assertEqual(source.stat().st_size, 512)

    def test_rejects_invalid_bntx_pointer(self):
        data = bytearray(make_bntx())
        struct.pack_into("<Q", data, 0x198, 0xFFFFFF)
        with self.assertRaises(scan.BNTXError):
            scan.parse_bntx(data, 0, "bad.bntx")


if __name__ == "__main__":
    unittest.main(verbosity=2)
