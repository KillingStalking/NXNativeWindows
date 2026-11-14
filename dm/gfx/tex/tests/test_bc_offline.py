#!/usr/bin/env python3
"""Smoke tests for the offline BC -> ASTC pack entry point."""
import ctypes
import importlib.util
import random
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "bc_offline_pack.py"
spec = importlib.util.spec_from_file_location("bc_offline_pack", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load offline packer at {SCRIPT}")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


@unittest.skipUnless(shutil.which("clang"), "requires clang")
class OfflineBC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bc-offline-test-")
        clang = shutil.which("clang")
        if clang is None:
            raise unittest.SkipTest("clang is unavailable")
        library = pack.build_decoder(clang, Path(cls.temp.name))
        cls.decode = pack.load_decoder(library)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_selected_formats_emit_one_astc_block_per_bc_block(self):
        rng = random.Random(0xBC7)
        cases = [
            ("BC1", 130, 8), ("BC1_SRGB", 131, 8),
            ("BC2", 132, 16), ("BC2_SRGB", 133, 16),
            ("BC3", 134, 16), ("BC3_SRGB", 135, 16),
            ("BC7", 152, 16), ("BC7_SRGB", 153, 16),
        ]
        for name, pf, block_bytes in cases:
            with self.subTest(format=name):
                # 5x5 pixels = 2x2 BC blocks; add row padding to exercise pitched input.
                src_pitch = 2 * block_bytes + 7
                src = bytearray(src_pitch + 2 * block_bytes)
                for y in range(2):
                    for x in range(2):
                        at = y * src_pitch + x * block_bytes
                        src[at:at + block_bytes] = bytes(rng.randrange(256) for _ in range(block_bytes))
                dst_pitch = 2 * 16
                dst = ctypes.create_string_buffer(dst_pitch * 2)
                src_buf = ctypes.create_string_buffer(bytes(src), len(src))
                rc = self.decode(pf, src_buf, src_pitch, 5, 5, dst, dst_pitch)
                self.assertEqual(rc, 0)
                self.assertEqual(len(dst.raw), 64)
                self.assertNotEqual(dst.raw, bytes(64))

    def test_nvn_block_linear_mip_uses_repo_deswizzler(self):
        # A single 4x4 BC block occupies the first bytes of one 512-byte GOB.
        rng = random.Random(0x4F)
        cases = [(0x41, 130, 8), (0x43, 132, 16), (0x44, 134, 16), (0x4D, 152, 16),
                 (0x45, 131, 8), (0x47, 133, 16), (0x48, 135, 16), (0x4E, 153, 16)]
        for nvn_format, _, block_bytes in cases:
            with self.subTest(nvn_format=hex(nvn_format)):
                storage = bytearray(512)
                storage[:block_bytes] = bytes(rng.randrange(256) for _ in range(block_bytes))
                src = ctypes.create_string_buffer(bytes(storage), len(storage))
                scratch = ctypes.create_string_buffer(block_bytes)
                dst = ctypes.create_string_buffer(16)
                dst_pitch, dst_size = ctypes.c_size_t(), ctypes.c_size_t()
                rc = self.decode.level(
                    1, nvn_format, 0, 1, 4, 4, 1, 1, 0, 0, 0, 0, 0,
                    src, len(storage), scratch, block_bytes, dst, 16,
                    ctypes.byref(dst_pitch), ctypes.byref(dst_size),
                )
                self.assertEqual(rc, 0)
                self.assertEqual(dst_pitch.value, 16)  # type: ignore[attr-defined]
                self.assertEqual(dst_size.value, 16)  # type: ignore[attr-defined]
                self.assertNotEqual(dst.raw, bytes(16))

    def test_runtime_only_formats_are_skipped_without_reading_source(self):
        for fmt in ("BC4", "BC4_S", "BC5", "BC5_S", "BC6H_S", "BC6H_U"):
            with self.subTest(format=fmt):
                entry = {"id": fmt, "format": fmt, "mip": 0, "layer": 0}
                result = pack.convert_entry(entry, Path("."), Path("out"), self.decode)
                self.assertTrue(result["skipped"])
                self.assertEqual(result["format"], fmt)

    def test_nvn_runtime_only_formats_are_skipped(self):
        for nvn_format in (0x49, 0x4A, 0x4B, 0x4C, 0x4F, 0x50):
            with self.subTest(nvn_format=hex(nvn_format)):
                entry = {"id": hex(nvn_format), "nvn": {"format": hex(nvn_format)}}
                result = pack.convert_entry(entry, Path("."), Path("out"), self.decode)
                self.assertTrue(result["skipped"])

    def test_nvn_mip_offsets_match_repo_layout(self):
        offset = self.decode.level_offset(1, 0x44, 0, 1, 64, 64, 1, 4, 0, 0, 0)
        self.assertEqual(offset, 0)
        wrong = {"id": "bad-layout", "source": "unused.bin", "offset": 0,
                 "storage_size": 65536, "mip": 0, "layer": 0,
                 "mip_offsets": [0, 123, 456, 789],
                 "nvn": {"target": 1, "format": "0x44", "width": 64, "height": 64,
                         "depth": 1, "levels": 4, "samples": 1}}
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "unused.bin").write_bytes(bytes(65536))
            with self.assertRaisesRegex(ValueError, "do not match"):
                pack.convert_entry(wrong, base, base / "out", self.decode)


    def test_valid_bntx_mip_offsets_are_accepted(self):
        width, height, levels = 64, 64, 4
        offsets = [
            int(self.decode.level_offset(1, 0x44, 0, 1, width, height, 1, levels, 0, 0, mip))
            for mip in range(levels)
        ]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "texture.bin").write_bytes(bytes(65536))
            entry = {
                "id": "valid-bntx-layout", "source": "texture.bin",
                "storage_size": 65536, "mip": 0, "layer": 0,
                "mip_offsets": offsets,
                "nvn": {
                    "target": 1, "format": "0x44", "width": width, "height": height,
                    "depth": 1, "levels": levels, "samples": 1, "flags": 0,
                    "stride": 0, "pkg_layout": 0,
                },
            }
            result = pack.convert_entry(entry, base, base / "out", self.decode)
            self.assertFalse(result.get("skipped", False))
            self.assertTrue(result["mip_offsets_validated"])
            self.assertEqual(result["size"], 4096)


    def test_bundle_results_writes_single_blob_and_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out = base / "pack"
            out.mkdir()
            (base / "source.bc").write_bytes(bytes(range(16)))
            result = pack.convert_entry(
                {"id": "one", "format": "BC3", "width": 4, "height": 4, "source": "source.bc"},
                base, out, self.decode,
            )
            astc_path = out / result["output"]
            astc_bytes = astc_path.read_bytes()
            pack_path = pack.bundle_results([result], out)
            self.assertEqual(pack_path.name, "bc_offline_pack.bin")
            self.assertEqual(pack_path.read_bytes(), astc_bytes)
            self.assertEqual(result["pack_offset"], 0)
            self.assertEqual(result["pack_size"], 16)
            self.assertNotIn("output", result)
            self.assertFalse(astc_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
