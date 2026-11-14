// Offline BC1/BC2/BC3/BC7 -> ASTC 4x4 transcoder.
// Uses the runtime BC decoder/transcoder and the same NVN layout/deswizzle code as Metal uploads.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "bc_soft.h"
#include "nvn_bc2astc.h"
#include "nvn_deswizzle.h"
#include "nvn_fmt_mtl.h"
#include "../nvn/nvn_tex.h"

#if defined(_WIN32)
#define BC_EXPORT __declspec(dllexport)
#else
#define BC_EXPORT
#endif

extern void bc_soft_astc_pixels(const uint8_t *rgba16, uint8_t *out);

static int selected_format(uint32_t pf) {
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
    const uint32_t blocks_x = (width + 3u) / 4u;
    const uint32_t blocks_y = (height + 3u) / 4u;
    const size_t src_row = (size_t)blocks_x * block_bytes;
    const size_t dst_row = (size_t)blocks_x * 16u;
    if (!src || !dst || !width || !height || src_pitch < src_row || dst_pitch < dst_row)
        return -2;
    for (uint32_t by = 0; by < blocks_y; ++by) {
        for (uint32_t bx = 0; bx < blocks_x; ++bx) {
            const uint8_t *block = src + (size_t)by * src_pitch + (size_t)bx * block_bytes;
            uint8_t *out = dst + (size_t)by * dst_pitch + (size_t)bx * 16u;
            if (kind) {
                nvn_bc2astc_block(kind, block, out);
            } else {
                uint8_t rgba[64];
                if (bc_soft_block(format, block, rgba, 16) != 0) return -3;
                bc_soft_astc_pixels(rgba, out);
            }
        }
    }
    return 0;
}

// Transcode one mip/layer directly from the complete Switch block-linear texture storage.
// 3D, multisampled, linear-render-target, and non-BC1/2/3/7 textures stay on the runtime path.
BC_EXPORT int bc_offline_texture_level(
    int target, int nvn_format, int flags, int samples,
    uint32_t width, uint32_t height, uint32_t depth, uint32_t levels, uint32_t stride,
    int pkg_layout, uint32_t level, uint32_t layer, int switch2,
    const uint8_t *storage, size_t storage_size,
    uint8_t *scratch, size_t scratch_size,
    uint8_t *dst, size_t dst_capacity, size_t *dst_pitch_out, size_t *dst_size_out) {
    if (!storage || !scratch || !dst || !dst_pitch_out || !dst_size_out ||
        !width || !height || !levels || level >= levels) return -10;
    if (target == 2 || target == 5 || target == 6 || target == 10 || (flags & 0x50) || samples > 1)
        return -11;
    uint32_t pf = nvn_fmt_bc_soft(nvn_format);
    if (!selected_format(pf)) return -12;

    nvn_texdesc d = {target, nvn_format, flags, samples, width, height, depth, levels, stride, pkg_layout};
    nvn_texlayout layout;
    nvn_tex_layout(&d, &layout);
    if (!layout.size || layout.size > storage_size || layer >= layout.layers) return -13;

    uint32_t w = width >> level; if (!w) w = 1;
    uint32_t h = height >> level; if (!h) h = 1;
    if (target == 0 || target == 3) h = 1;
    int bw = 0, bh = 0;
    uint32_t bpb = (uint32_t)nvn_fmt_bpb(nvn_format, &bw, &bh);
    if (!bpb || bw != 4 || bh != 4) return -14;
    uint32_t blocks_x = (w + 3u) / 4u, blocks_y = (h + 3u) / 4u;
    size_t linear_pitch = (size_t)blocks_x * bpb;
    size_t linear_size = linear_pitch * blocks_y;
    size_t astc_pitch = (size_t)blocks_x * 16u;
    size_t astc_size = astc_pitch * blocks_y;
    if (scratch_size < linear_size || dst_capacity < astc_size) return -15;

    nvn_gob_switch2 = switch2 != 0;
    if (nvn_deswizzle(&d, level, layer, storage, scratch, linear_pitch) != 0) return -16;
    int rc = bc_offline_texture(pf, scratch, linear_pitch, w, h, dst, astc_pitch);
    if (rc) return -17;
    *dst_pitch_out = astc_pitch;
    *dst_size_out = astc_size;
    return 0;
}
