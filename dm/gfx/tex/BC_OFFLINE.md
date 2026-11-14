# Offline BC texture pack (experimental)

This branch adds an offline transcoding stage for BC1/BC2/BC3/BC7. It reuses the existing BC1–3 integer transcoder, BC7 software decoder, ASTC encoder, and NVN texture layout/deswizzle implementation.

## Current scope

- BC1 / BC1 sRGB -> ASTC 4x4.
- BC2 / BC2 sRGB -> ASTC 4x4.
- BC3 / BC3 sRGB -> ASTC 4x4.
- BC7 / BC7 sRGB -> BC7 software decode, then ASTC 4x4 LDR encode.
- BC4 / BC5 / BC6H are rejected intentionally; they remain on the runtime decoder.

The converter writes one raw ASTC block stream per manifest entry and a JSON index. It never modifies the NSP, RomFS, or source files. Give each mip level and array/cubemap face its own entry.

## NVN block-linear input manifest (recommended)

An NVN entry describes the complete texture storage in the source file. The converter uses nvn_tex_layout / nvn_deswizzle to extract the requested mip and layer before transcoding. Provide the actual NVN texture descriptor values, including pkg_layout when the game supplied a packaged layout. The source range must contain the whole texture allocation (all mips and layers), not just one BC subresource.

```json
{
  "version": 1,
  "textures": [
    {
      "id": "example_bc3_mip0_layer0",
      "source": "texture_storage.bin",
      "offset": 0,
      "storage_size": 1048576,
      "mip": 0,
      "layer": 0,
      "nvn": {
        "target": 1,
        "format": "0x44",
        "flags": 0,
        "samples": 1,
        "width": 256,
        "height": 256,
        "depth": 1,
        "levels": 9,
        "stride": 0,
        "pkg_layout": 0,
        "switch2": false
      }
    }
  ]
}
```

NVN format codes recognized by the tool: 0x41/0x42 BC1, 0x43 BC2, 0x44 BC3, 0x45–0x48 their sRGB variants, 0x4D BC7, and 0x4E BC7 sRGB. BC6H uses 0x4F/0x50 and is intentionally not converted. The texture descriptor's format is authoritative; an optional top-level format label must agree with it.

## Linear input manifest (for tests / already-deswizzled dumps)

An entry without an nvn object expects BC blocks already in linear block-row order. row_pitch is the byte stride between BC block rows and may include padding.

```json
{
  "version": 1,
  "textures": [
    {
      "id": "linear_bc3_mip0_layer0",
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

The build environment needs Python 3 and the clang + lld toolchain used by the Windows build.

```powershell
python dm/gfx/tools/bc_offline_pack.py --manifest path\to\manifest.json --out path\to\bc_astc_pack
```

If clang is not on PATH, pass --clang with the clang.exe path. A prebuilt compatible DLL can be supplied with --decoder-dll.

Output:
- one .astc file per texture subresource (raw ASTC 4x4 blocks; no ASTC file header);
- bc_offline_pack.json with dimensions, mip/layer, ASTC format, layout, and SHA-256 hashes.

## Integration boundary

This is an **offline converter prototype**, not yet an end-to-end game-data replacement feature. It can deswizzle a texture when a correct NVN descriptor and complete storage range are supplied, but it does not yet discover texture descriptors or storage offsets inside NSP/RomFS automatically. It also does not rewrite game descriptors or make the Metal runtime load the sidecar pack. The generated pack is therefore standalone; the game continues using the existing runtime decoder.

Next stages:
1. Add a scanner for the supported game texture containers (including BNTX where applicable) to produce correct NVN descriptors and storage offsets.
2. Validate sparse, packaged-layout, array/cubemap, mip-tail, and Switch 2 cases against real game textures.
3. Add a stable runtime resource key and sidecar lookup with the original BC upload as a fallback.
4. Package the sidecar independently of the IPA so the existing split-data flow can carry it.

Do not mark a texture for offline conversion unless its descriptor and complete storage range are known. Unsupported layouts must fall back to the runtime path.
