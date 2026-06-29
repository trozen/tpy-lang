#pragma once
// Hand-written mbedTLS facade for TPy bindings.
//
// We deliberately do NOT `#include` any <mbedtls/*.h> here. mbedTLS defines
// a large set of `MBEDTLS_*` macros that would collide with TPy
// module-level constants of the same name (the preprocessor would expand
// the macro at every TPy `inline constexpr uint32_t MBEDTLS_... = ...;`
// declaration site, producing a syntax error). By mirroring only the
// symbols and types the ssl bindings use, no mbedTLS header ever enters a
// TPy-generated translation unit -- the macros never get a chance to fire.
//
// Opaque struct types stay forward-declared: TPy code only ever passes
// pointers (`Ptr[SslContext]` -> `mbedtls_ssl_context*`) across the
// boundary, never dereferences them. The real struct definitions live in
// the vendored mbedTLS .c files (compiled separately, their TUs include the
// real headers); the linker connects these `extern "C"` declarations to
// those symbols.
//
// v1 surface is intentionally minimal -- the SSLContext / SSLSocket binding
// surface (ssl / x509 / pk / entropy / ctr_drbg / net handles plus
// handshake / read / write) is added alongside the ssl module.

#include <cstdint>

extern "C" {
    // Compiled-in mbedTLS version as 0xMMNNPP00 (major/minor/patch).
    unsigned int mbedtls_version_get_number(void);
}
