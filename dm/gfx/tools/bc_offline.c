// Offline BC1/BC2/BC3/BC7 -> ASTC 4x4 transcoder.
// Uses the same NVN layout/deswizzle and BC decode/transcode code as the runtime path.
#include <stddef.h>
#include <stdint.h>
#include "bc_soft.h"
#include "nvn_bc2astc.h"
#include "nvn_deswizzle.h"
#include "nvn_fmt_mtl.h"
#include "../nvn/nvn_tex.h"
#include "nvn_texkey.h"

#if defined(_WIN32)
#define BC_EXPORT __declspec(dllexport)
#else
#define BC_EXPORT
#endif

// nvn_bc_soft is defined by bc_soft.c. Set it only while the standalone
// converter is running; the normal runtime controls it from device capabilities.

static int is_bc_astc_target(uint32_t pf) {
    switch (pf) {
        case 130: case 131: case 132: case 133:
        case 134: case 135: case 152: case 153: return 1;
        default: return 0;
    }
}

// Transcode one already-linear BC subresource. src_pitch is bytes per BC block row.
BC_EXPORT int bc_offline_texture(uint32_t format, const uint8_t *src,
                                 size_t src_pitch, uint32_t width, uint32_t height,
                                 uint8_t *dst, size_t dst_pitch) {
    int kind = 0;
    size_t block_bytes = 16;
    switch (format) {
        case 130: case 131: kind = 1; block_bytes = 8; break;
        case 132: case 133: kind = 2; break;
        case 134: case 135: kind = 3; break;
        case 152: case 153: break;
        default: return -1;
    }
    if (!src || !dst || !width || !height) return -2;
    const uint32_t blocks_x = (width + 3u) / 4u;
    const uint32_t blocks_y = (height + 3u) / 4u;
    const size_t src_row = (size_t)blocks_x * block_bytes;
    const size_t dst_row = (size_t)blocks_x * 16u;
    if (src_pitch < src_row || dst_pitch < dst_row) return -3;
    for (uint32_t by = 0; by < blocks_y; ++by) {
        for (uint32_t bx = 0; bx < blocks_x; ++bx) {
            const uint8_t *block = src + (size_t)by * src_pitch + (size_t)bx * block_bytes;
            uint8_t *out = dst + (size_t)by * dst_pitch + (size_t)bx * 16u;
            if (kind) {
                nvn_bc2astc_block(kind, block, out);
            } else {
                uint8_t rgba[64];
                if (bc_soft_block(format, block, rgba, 16) != 0) return -4;
                bc_soft_astc_pixels(rgba, out);
            }
        }
    }
    return 0;
}

// Convert one mip/layer from the complete BNTX/NVN block-linear allocation.
// Unsupported dimensions/layouts return an error; the caller must keep runtime BC.
BC_EXPORT int bc_offline_texture_level(
    int target, int nvn_format, int flags, int samples,
    uint32_t width, uint32_t height, uint32_t depth, uint32_t levels, uint32_t stride,
    int pkg_layout, uint32_t level, uint32_t layer, int switch2,
    const uint8_t *storage, size_t storage_size,
    uint8_t *scratch, size_t scratch_size,
    uint8_t *dst, size_t dst_capacity, size_t *dst_pitch_out, size_t *dst_size_out) {
    if (!storage || !scratch || !dst || !dst_pitch_out || !dst_size_out ||
        !width || !height || !levels || level >= levels) return -10;
    if (target == 2 || target == 5 || target == 6 || target == 10 ||
        (flags & 0x50) || samples > 1) return -11;

    nvn_gob_switch2 = switch2 != 0;
    nvn_bc_soft = 1;
    uint32_t pf = nvn_fmt_bc_soft(nvn_format);
    if (!is_bc_astc_target(pf)) return -12;

    nvn_texdesc d = {target, nvn_format, flags, samples, width, height, depth,
                     levels, stride, pkg_layout};
    nvn_texlayout layout;
    nvn_tex_layout(&d, &layout);
    if (!layout.size || layout.size > storage_size || layer >= layout.layers) return -13;

    uint32_t row_bytes = 0, rows = 0, slices = 0, bpb = 0;
    uint64_t base = 0;
    unsigned tileH = 0, tileD = 0;
    if (nvn_deswizzle_geom(&d, level, layer, &row_bytes, &rows, &slices,
                           &base, &tileH, &tileD, &bpb) != 0) return -14;
    if (slices != 1 || base > storage_size || !bpb) return -15;

    uint32_t w = width >> level; if (!w) w = 1;
    uint32_t h = (target == 0 || target == 3) ? 1 : (height >> level);
    if (!h) h = 1;
    const size_t linear_pitch = (size_t)((w + 3u) / 4u) * bpb;
    const size_t linear_size = linear_pitch * rows;
    const size_t astc_pitch = (size_t)((w + 3u) / 4u) * 16u;
    const size_t astc_size = astc_pitch * rows;
    if (row_bytes != linear_pitch || rows != (h + 3u) / 4u ||
        scratch_size < linear_size || dst_capacity < astc_size) return -16;

    if (nvn_deswizzle(&d, level, layer, storage, scratch, linear_pitch) != 0) return -17;
    int rc = bc_offline_texture(pf, scratch, linear_pitch, w, h, dst, astc_pitch);
    if (rc) return -18;
    *dst_pitch_out = astc_pitch;
    *dst_size_out = astc_size;
    return 0;
}

BC_EXPORT uint64_t bc_offline_level_offset(
    int target, int nvn_format, int flags, int samples,
    uint32_t width, uint32_t height, uint32_t depth, uint32_t levels,
    uint32_t stride, int pkg_layout, uint32_t level) {
    if (!width || !height || !levels || level >= levels) return UINT64_MAX;
    nvn_gob_switch2 = 0;
    nvn_texdesc d = {target, nvn_format, flags, samples, width, height, depth,
                     levels, stride, pkg_layout};
    nvn_texlayout layout;
    nvn_tex_layout(&d, &layout);
    if (!layout.size) return UINT64_MAX;
    return nvn_tex_level_offset(&d, level);
}

BC_EXPORT uint64_t bc_offline_texture_key(
    int target, int nvn_format, int flags, int samples,
    uint32_t width, uint32_t height, uint32_t depth, uint32_t levels,
    uint32_t stride, int pkg_layout, const uint8_t *storage, size_t storage_size) {
    if (!storage || !width || !height || !levels) return UINT64_MAX;
    nvn_gob_switch2 = 0;
    nvn_texdesc d = {target, nvn_format, flags, samples, width, height, depth,
                     levels, stride, pkg_layout};
    nvn_texlayout layout;
    nvn_tex_layout(&d, &layout);
    if (!layout.size || layout.size > storage_size) return UINT64_MAX;
    return nvn_texkey(storage, layout.size, (uint32_t)nvn_format, width, height, levels);
}
