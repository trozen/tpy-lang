// TPy-owned glue for zlib (NOT upstream source).
//
// The zlib/gzip modules' FFI surface. TPy cannot allocate a z_stream (a C
// struct whose layout and alloc hooks belong to zlib), so this shim owns one
// behind an opaque `tpy_zstream` and exposes a stateless step call: every
// call passes its input and output buffers, so the stream only carries
// zlib's internal state and the TPy side owns every byte it sees.
//
// Compiled with the C compiler against <zlib.h> (vendored or system, see
// tpyc/build/zlib.py) in its own TU, so zlib's macros (Z_OK, MAX_WBITS, ...)
// never reach a TPy-generated TU. Declared to TPy via
// runtime/cpp/include/tpy/stdlib/zlib_h.hpp; never include that facade here.
#include <stdint.h>
#include <stdlib.h>

#include <zlib.h>

struct tpy_zstream {
    z_stream zs;
    int deflating;
};

// avail_in / avail_out are 32-bit uInt; larger buffers are fed in slices.
#define TPY_ZS_MAX_SLICE ((uint64_t)1 << 30)

static struct tpy_zstream *tpy_zs_alloc(int deflating) {
    struct tpy_zstream *s = (struct tpy_zstream *)calloc(1, sizeof(*s));
    if (s != NULL) {
        s->deflating = deflating;
    }
    return s;
}

struct tpy_zstream *tpy_zs_inflate_new(int32_t wbits, int32_t *rc) {
    struct tpy_zstream *s = tpy_zs_alloc(0);
    if (s == NULL) {
        *rc = Z_MEM_ERROR;
        return NULL;
    }
    *rc = inflateInit2(&s->zs, wbits);
    if (*rc != Z_OK) {
        free(s);
        return NULL;
    }
    return s;
}

struct tpy_zstream *tpy_zs_deflate_new(int32_t level, int32_t method,
                                       int32_t wbits, int32_t memlevel,
                                       int32_t strategy, int32_t *rc) {
    struct tpy_zstream *s = tpy_zs_alloc(1);
    if (s == NULL) {
        *rc = Z_MEM_ERROR;
        return NULL;
    }
    *rc = deflateInit2(&s->zs, level, method, wbits, memlevel, strategy);
    if (*rc != Z_OK) {
        free(s);
        return NULL;
    }
    return s;
}

// One inflate()/deflate() call over (in, out). Reports how much input was
// consumed and output produced; returns zlib's code. `flush` is passed
// through only on the slice that carries the tail of the input, so a
// Z_FINISH over a >1 GiB input is not issued before the input is complete.
int32_t tpy_zs_step(struct tpy_zstream *s, const uint8_t *in, uint64_t in_len,
                    uint8_t *out, uint64_t out_cap, int32_t flush,
                    uint64_t *consumed, uint64_t *produced) {
    uint64_t in_slice = in_len < TPY_ZS_MAX_SLICE ? in_len : TPY_ZS_MAX_SLICE;
    uint64_t out_slice = out_cap < TPY_ZS_MAX_SLICE ? out_cap : TPY_ZS_MAX_SLICE;
    int step_flush = in_slice == in_len ? flush : Z_NO_FLUSH;
    int rc;
    s->zs.next_in = (Bytef *)in;
    s->zs.avail_in = (uInt)in_slice;
    s->zs.next_out = out;
    s->zs.avail_out = (uInt)out_slice;
    rc = s->deflating ? deflate(&s->zs, step_flush) : inflate(&s->zs, step_flush);
    *consumed = in_slice - s->zs.avail_in;
    *produced = out_slice - s->zs.avail_out;
    return rc;
}

// zlib's message for the last error, or NULL when it set none. Strings are
// returned as `const uint8_t *`, the type the facade and the TPy binding
// (`Ptr[readonly[uint8]]`) declare.
const uint8_t *tpy_zs_msg(struct tpy_zstream *s) {
    return (const uint8_t *)s->zs.msg;
}

void tpy_zs_free(struct tpy_zstream *s) {
    if (s == NULL) {
        return;
    }
    if (s->deflating) {
        deflateEnd(&s->zs);
    } else {
        inflateEnd(&s->zs);
    }
    free(s);
}

uint32_t tpy_zlib_crc32(uint32_t value, const uint8_t *data, uint64_t len) {
    uLong v = value;
    while (len > 0) {
        uint64_t n = len < TPY_ZS_MAX_SLICE ? len : TPY_ZS_MAX_SLICE;
        v = crc32(v, data, (uInt)n);
        data += n;
        len -= n;
    }
    return (uint32_t)v;
}

uint32_t tpy_zlib_adler32(uint32_t value, const uint8_t *data, uint64_t len) {
    uLong v = value;
    while (len > 0) {
        uint64_t n = len < TPY_ZS_MAX_SLICE ? len : TPY_ZS_MAX_SLICE;
        v = adler32(v, data, (uInt)n);
        data += n;
        len -= n;
    }
    return (uint32_t)v;
}

// Version of the zlib linked at runtime (CPython's ZLIB_RUNTIME_VERSION).
const uint8_t *tpy_zlib_runtime_version(void) {
    return (const uint8_t *)zlibVersion();
}

// Version of the zlib.h this shim was compiled against (ZLIB_VERSION).
const uint8_t *tpy_zlib_header_version(void) {
    return (const uint8_t *)ZLIB_VERSION;
}
