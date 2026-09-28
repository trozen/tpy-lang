#pragma once
// Hand-written zlib facade for TPy bindings.
//
// We deliberately do NOT `#include <zlib.h>` here. zlib.h `#define`s names
// the public `zlib` module also declares as constants (Z_OK, MAX_WBITS,
// Z_FINISH, ...) -- the preprocessor would expand them at every TPy
// `inline constexpr int32_t MAX_WBITS = ...;` declaration and break the
// build. So this facade declares only the `tpy_zs_*` / `tpy_zlib_*` shim
// API (runtime/cpp/src/stdlib/zlib_shim.c), whose TU includes the real
// zlib.h; the linker connects these `extern "C"` declarations to it.
//
// The shim owns each z_stream behind an opaque `tpy_zstream`: TPy cannot
// allocate zlib's C struct itself. Lengths are std::uint64_t to match the
// TPy uint64 bindings exactly (see mbedtls_h.hpp for the size_t reasoning).

#include <cstdint>

struct tpy_zstream;

extern "C" {

// NULL on failure, with zlib's init code (Z_STREAM_ERROR, Z_MEM_ERROR, ...)
// stored through `rc`.
tpy_zstream *tpy_zs_inflate_new(std::int32_t wbits, std::int32_t *rc);
tpy_zstream *tpy_zs_deflate_new(std::int32_t level, std::int32_t method,
                                std::int32_t wbits, std::int32_t memlevel,
                                std::int32_t strategy, std::int32_t *rc);

std::int32_t tpy_zs_step(tpy_zstream *s, const std::uint8_t *in,
                         std::uint64_t in_len, std::uint8_t *out,
                         std::uint64_t out_cap, std::int32_t flush,
                         std::uint64_t *consumed, std::uint64_t *produced);

const std::uint8_t *tpy_zs_msg(tpy_zstream *s);
void tpy_zs_free(tpy_zstream *s);

std::uint32_t tpy_zlib_crc32(std::uint32_t value, const std::uint8_t *data,
                             std::uint64_t len);
std::uint32_t tpy_zlib_adler32(std::uint32_t value, const std::uint8_t *data,
                               std::uint64_t len);

const std::uint8_t *tpy_zlib_runtime_version(void);
const std::uint8_t *tpy_zlib_header_version(void);

}  // extern "C"
