# Offline BC texture pack (experimental)

This branch contains an offline BC texture conversion prototype. It reuses the repository's BC1–3 integer transcoder, BC7 software decoder, ASTC encoder, and NVN texture layout/deswizzle implementation.

## Format policy

- BC1 / BC1 sRGB -> ASTC 4x4.
- BC2 / BC2 sRGB -> ASTC 4x4.
- BC3 / BC3 sRGB -> ASTC 4x4.
- BC7 / BC7 sRGB -> software decode, then ASTC 4x4 LDR encoding.
- BC4 / BC5 / BC6H are recorded as runtime-only entries and are not converted.

The original game files are read-only. The scanner extracts each eligible BNTX texture allocation into a separate source file; the packer writes raw ASTC block streams and a JSON index.

## One-command scan and pack

From the repository root on Windows:

```powershell
python dm/gfx/tools/bc_offline_build.py --root "D:\\games\\title\\RomFS.img" --out "D:\\games\\title\\offline-bc" --clang "C:\\toolchains\\llvm\\bin\\clang.exe"
```

This runs the scanner and packer in sequence. It creates `offline-bc/scan/` for extracted source allocations and reports, and `offline-bc/bc_offline_pack/` for the two runtime sidecar files. Set `DM_BC_OFFLINE_PACK` to that final `bc_offline_pack` directory when building the app.

## Automatic BNTX scan

The scanner walks an extracted RomFS directory or the repository's read-only `RomFS.img` view. It looks for BNTX containers embedded in files and also scans Yaz0-compressed payloads, including compressed SARC members. It does not currently decompress every possible game archive/compression format.

On Windows:

```powershell
python dm/gfx/tools/bntx_bc_scan.py --root "D:\games\extracted\RomFS" --out "D:\games\bc_offline"
```

You can also pass the repository's `RomFS.img` directly:

```powershell
python dm/gfx/tools/bntx_bc_scan.py --root "D:\games\extracted\RomFS.img" --out "D:\games\bc_offline"
```

The scanner creates:
- `bc_offline_manifest.json`: one entry per supported mip and array/cube layer;
- `sources\*.bin`: extracted source allocations for BC1/2/3/7;
- `bntx_scan_summary.json`: container, texture, and skip counts.

It preserves BNTX channel mapping, mip pointer offsets, texture dimensions, array/cube shape, and the packaged layout bytes. Before conversion, the packer compares every BNTX mip pointer offset against `nvn_tex_level_offset()`. A mismatch is a hard error: that texture is not converted using a guessed layout.

## Convert to ASTC

The build environment needs Python 3 and clang. The helper builds a small native library from this repository's C sources (a Windows DLL on Windows; a native test library on macOS/Linux).

```powershell
python dm/gfx/tools/bc_offline_pack.py --manifest "D:\games\bc_offline\bc_offline_manifest.json" --out "D:\games\bc_offline\astc"
```

If clang is not on PATH, pass `--clang C:\path\to\clang.exe`. A compatible prebuilt library can be supplied with `--decoder-dll`.

The output directory contains:
- `bc_offline_pack.bin`: one concatenated raw ASTC block stream for all converted subresources;
- `bc_offline_pack.json`: subresource offsets/sizes, runtime texture keys, dimensions, mip/layer, NVN layout, original channel mapping, and SHA-256 hashes.

The single binary plus index avoids thousands of small files and is suitable for split-data installs.

## Supported layout boundary

The automatic scanner currently converts only non-sparse, swizzled BNTX textures with supported 1D/2D/cube/array dimensions and single-sample storage. It skips linear, sparse, 3D, multisample, and unknown texture layouts. BC4/BC5/BC6H are left for the runtime decoder.

The manifest also supports already-linear BC subresources for isolated tests. Such entries must describe BC block-row order and provide a correct `row_pitch`; do not pass Tegra block-linear bytes through the linear-input path.

## Runtime loading and split-data

The runtime sidecar lookup is now wired into the packaged NVN texture upload path:

- The packer calculates the same `nvn_texkey` used by the runtime.
- The Metal upload path checks that every required mip/layer exists and that its ASTC dimensions, row pitch, size, and format match.
- A matching texture uploads directly from the mmap-backed sidecar blob as ASTC, bypassing the runtime BC transcode.
- A missing, partial, mismatched, unsupported, or memory-allocation-failed entry falls back to the existing runtime decoder.
- BC4/BC5/BC6H remain on the runtime decoder. BC7 uses ASTC only when a complete sidecar is present and ASTC mode is enabled; otherwise its existing RGBA8 path remains active.

The app looks for `bc_offline_pack/bc_offline_pack.json` and `bc_offline_pack/bc_offline_pack.bin` in the app bundle or in the split-data/Documents location.

### Split-data build

For the Windows build tool, set `DM_BC_OFFLINE_PACK` to the packer output directory. The normal app build bundles it; the split-data build adds it under `bc_offline_pack/` in the game data folder.

For the iOS build scripts, set `DM_BC_OFFLINE_PACK` to the packer output directory. With `DM_COPY_ROMFS=NO`, the default is to keep the sidecar outside the IPA; `scripts/push_romfs.sh <UDID> <bundle-id> <resources.env>` copies the binary and index atomically to `Documents/bc_offline_pack` over AFC. With `DM_COPY_BC_OFFLINE=YES`, it is bundled in the app instead.

## Remaining validation

This is still experimental. Before relying on it for a game:
1. Run the scanner against that game's extracted RomFS.
2. Inspect `bntx_scan_summary.json` and the generated manifest; unsupported dimensions/layouts are deliberately skipped.
3. Run the packer and confirm the number of converted and runtime-only subresources.
4. Build and install with the sidecar in the selected bundle/split-data location.
5. Verify real BC1/2/3/7 textures render correctly and that a missing/corrupt sidecar cleanly falls back to runtime decoding.

The scanner does not yet decompress every possible game archive format, and it cannot guarantee that every texture in every title is BNTX-backed. Do not replace the original game data; the sidecar is optional and the original BC assets remain the fallback.
