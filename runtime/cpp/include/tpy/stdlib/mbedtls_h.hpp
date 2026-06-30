#pragma once
// Hand-written mbedTLS facade for TPy bindings.
//
// We deliberately do NOT `#include` any <mbedtls/*.h> here. mbedTLS defines
// a large set of `MBEDTLS_*` macros that would collide with TPy
// module-level constants of the same name (the preprocessor would expand
// the macro at every TPy declaration site, producing a syntax error). So
// this facade declares only the symbols the `ssl` bindings call, and no
// mbedTLS header ever enters a TPy-generated translation unit.
//
// The bulk of the surface is the cohesive `tpy_tls_*` session API
// implemented in runtime/cpp/src/stdlib/mbedtls_shim.c: TPy cannot
// stack-allocate mbedTLS context structs nor pass C function pointers, so
// the shim bundles every sub-object behind one opaque `tpy_tls_session`
// and does the function-pointer wiring in C. The `ssl` module holds a
// single `Ptr[TlsSession]` and stays backend-agnostic. The shim's TU
// includes the real mbedTLS headers; the linker connects these `extern "C"`
// declarations to it (and to the vendored mbedTLS objects).

#include <cstdint>

// Opaque session handle -- TPy only ever holds/passes `tpy_tls_session*`.
struct tpy_tls_session;

extern "C" {

// Compiled-in mbedTLS version as 0xMMNNPP00 (major/minor/patch).
unsigned int mbedtls_version_get_number(void);

// Lifetime.
tpy_tls_session *tpy_tls_new(void);
void tpy_tls_free(tpy_tls_session *s);

// Configuration (string args are (view ptr, length), not NUL-terminated).
// Lengths are std::uint64_t to match the TPy UInt64 bindings exactly (the
// shim defines them size_t -- ABI-identical on every 64-bit target; the
// linker resolves the extern "C" name, and uint64_t avoids a uint64_t-vs-
// size_t mismatch on platforms where they are distinct types).
int tpy_tls_config_client(tpy_tls_session *s, const unsigned char *ca_path,
                          std::uint64_t ca_path_len, int verify);
int tpy_tls_config_server(tpy_tls_session *s,
                          const unsigned char *cert_path, std::uint64_t cert_path_len,
                          const unsigned char *key_path, std::uint64_t key_path_len);
int tpy_tls_setup(tpy_tls_session *s);
void tpy_tls_set_fd(tpy_tls_session *s, int fd);
int tpy_tls_set_hostname(tpy_tls_session *s, const unsigned char *host,
                         std::uint64_t host_len);

// Handshake + application data (return mbedTLS codes; classify with
// tpy_tls_classify). read/write return a byte count when >= 0.
int tpy_tls_handshake(tpy_tls_session *s);
int tpy_tls_read(tpy_tls_session *s, unsigned char *buf, std::uint64_t len);
int tpy_tls_write(tpy_tls_session *s, const unsigned char *buf, std::uint64_t len);
int tpy_tls_close_notify(tpy_tls_session *s);

// Introspection + error handling.
std::uint32_t tpy_tls_verify_result(tpy_tls_session *s);
const char *tpy_tls_version(tpy_tls_session *s);
int tpy_tls_classify(int rc);
void tpy_tls_strerror(int code, unsigned char *buf, std::uint64_t len);

}  // extern "C"
