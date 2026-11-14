# Offline BC texture pack (experimental)

This branch adds the first offline-transcoding stage for BC1/BC2/BC3/BC7.

## Current scope

- BC1 / BC1 sRGB: existing integer BC1 -> ASTC 4x4 transcoder.
- BC2 / BC2 sRGB: existing integer BC2 -> ASTC 4x4 transcoder.
- BC3 / BC3 sRGB: existing integer BC3 -> ASTC 4x4 transcoder.
- BC7 / BC7 sRGB: existing BC7 software decoder followed by the existing ASTC 4x4 LDR encoder.
- BC4 / BC5 / BC6H: rejected intentionally; these remain on the runtime path.

The converter writes one raw ASTC block stream per manifest entry and a JSON index. It does not modify the NSP, RomFS, or source subresource files. Give each mip level and array/cubemap face its own manifest entry.

## Input manifest

Each source must already be in **linear BC block-row order** (not Tegra block-linear / GOB order). The row_pitch is the byte stride between BC block rows; it may include padding. The offset is a byte offset in the source file.

```json
{
  "version": 1,
  "textures": [
    {
      "id": "albedo_mip0_layer0",
      "format": "BC3_SRGB",
      "width": 1024,
      "height": 512,
      "source": "linear/albedo.bc",
      "offset": 0,
      "row_pitch": 4096,
      "mip": 0,
      "layer": 0
    }
  ]
}
```

## Run on Windows

The build environment needs Python 3 and the same clang + lld toolchain used by the Windows build.

```powershell
python dm/gfx/tools/bc_offline_pack.py --manifest path\to\manifest.json --out path\to\bc_astc_pack
```

If clang is not on PATH:

```powershell
python dm/gfx/tools/bc_offline_pack.py --manifest path\to\manifest.json --out path\to\bc_astc_pack --clang C:\path\to\clang.exe
```

Output:
- one .astc file per texture subresource (raw ASTC 4x4 blocks; no ASTC file header);
- bc_offline_pack.json, containing dimensions, mip/layer, format, row pitch, and SHA-256 hashes.

## Important integration boundary

This is a **converter prototype**, not yet an end-to-end game-data replacement feature. It currently expects a manifest produced by a future NVN texture-resource scanner and deswizzler. It does not yet discover texture descriptors in NSP/RomFS, convert Tegra GOB layout, rewrite NVN texture descriptors, or make the Metal runtime load the sidecar pack. Until those stages are implemented, the generated pack is a standalone artifact and the game continues using the existing runtime decoder.

The next required stages are:
1. Scan/capture the game's BC texture descriptors and identify each mip/layer's source range.
2. Reproduce the exact nvn_tex_layout / nvn_deswizzle layout, including packaged layout parameters, sparse textures, 3D textures, and mip tails.
3. Add a stable resource key and a runtime sidecar lookup, preserving the original BC fallback when a key is absent.
4. Package the sidecar independently of the IPA so the existing split-data flow can carry it.

Do not feed the converter block-linear bytes directly: that would create corrupted ASTC output.
