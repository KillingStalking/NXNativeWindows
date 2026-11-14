# Offline BC texture pack (experimental)

This branch contains an offline BC texture conversion prototype. It reuses the repository's BC1–3 integer transcoder, BC7 software decoder, ASTC encoder, and NVN texture layout/deswizzle implementation.

## Format policy

- BC1 / BC1 sRGB -> ASTC 4x4.
- BC2 / BC2 sRGB -> ASTC 4x4.
- BC3 / BC3 sRGB -> ASTC 4x4.
- BC7 / BC7 sRGB -> software decode, then ASTC 4x4 LDR encoding.
- BC4 / BC5 / BC6H are recorded as runtime-only entries and are not converted.

The original game files are read-only. The scanner extracts each eligible BNTX texture allocation into a separate source file; the packer writes raw ASTC block streams and a JSON index.

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
- one `.astc` raw block stream per converted texture subresource (no ASTC file header);
- `bc_offline_pack.json`, including dimensions, mip/layer, NVN layout, original channel mapping, and SHA-256 hashes.

## Supported layout boundary

The automatic scanner currently converts only non-sparse, swizzled BNTX textures with supported 1D/2D/cube/array dimensions and single-sample storage. It skips linear, sparse, 3D, multisample, and unknown texture layouts. BC4/BC5/BC6H are left for the runtime decoder.

The manifest also supports already-linear BC subresources for isolated tests. Such entries must describe BC block-row order and provide a correct `row_pitch`; do not pass Tegra block-linear bytes through the linear-input path.

## Runtime integration still required

This is not yet an end-to-end game-data replacement feature. It currently:
1. discovers eligible BNTX containers in the scanned filesystem and Yaz0 payloads;
2. extracts their texture allocations;
3. validates mip offsets against the repository NVN layout;
4. transcodes eligible BC subresources into ASTC sidecar files.

It does **not** yet rewrite the game's texture resources, load the ASTC sidecar in the Metal runtime, or package the sidecar into the existing split-data installation flow. The game therefore continues to use its current runtime BC decoder. Do not replace game data with this pack until the runtime lookup/fallback and split-data packaging stages are implemented and tested on real game assets.
