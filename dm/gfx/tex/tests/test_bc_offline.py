#!/usr/bin/env python3
"""Smoke tests for the offline BC -> ASTC pack entry point (Windows + clang/lld)."""
import ctypes
import importlib.util
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "bc_offline_pack.py"
spec = importlib.util.spec_from_file_location("bc_offline_pack", SCRIPT)
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


@unittest.skipUnless(os.name == "nt" and shutil.which("clang"), "requires Windows clang/lld")
class OfflineBC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bc-offline-test-")
        dll = pack.build_decoder(shutil.which("clang"), Path(cls.temp.name))
        cls.decode = pack.load_decoder(dll)

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
                self.assertEqual(dst_pitch.value, 16)
                self.assertEqual(dst_size.value, 16)
                self.assertNotEqual(dst.raw, bytes(16))

    def test_runtime_only_formats_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "unused.bin").write_bytes(b"")
            for fmt in ("BC4", "BC5", "BC6H"):
                with self.subTest(format=fmt):
                    entry = {"id": fmt, "format": fmt, "width": 4, "height": 4, "source": "unused.bin"}
                    with self.assertRaisesRegex(ValueError, "intentionally left to the runtime decoder"):
                        pack.convert_entry(entry, base, base / "out", self.decode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
