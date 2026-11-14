// Offline BC1/BC2/BC3/BC7 -> ASTC 4x4 transcoder.
// Uses the same integer BC1-3 transcoder and BC7 decoder / ASTC encoder as the runtime path.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "bc_soft.h"
#include "nvn_bc2astc.h"

#if defined(_WIN32)
#define BC_EXPORT __declspec(dllexport)
#else
#define BC_EXPORT
#endif

// NVN/Metal format IDs used by bc_soft.c:
// BC1=130, BC1_S=131, BC2=132, BC2_S=133, BC3=134, BC3_S=135, BC7=152, BC7_S=153.
extern void bc_soft_astc_pixels(const uint8_t *rgba16, uint8_t *out);

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
        default: return -1; // BC4/5/6H intentionally remain on the runtime decoder.
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
