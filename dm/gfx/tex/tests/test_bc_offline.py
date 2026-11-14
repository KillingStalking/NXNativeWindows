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
