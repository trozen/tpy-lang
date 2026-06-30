// TPy-owned glue for the vendored mbedTLS library (NOT upstream source).
//
// The ssl module's FFI surface. TPy cannot stack-allocate mbedTLS context
// structs nor pass C function pointers (entropy_func / ctr_drbg_random /
// net_send / net_recv), so this shim:
//   * bundles every mbedTLS sub-object a TLS session needs behind one opaque
//     `tpy_tls_session`, exposing cohesive ops (new/config/setup/handshake/
//     read/write/...); the TPy `ssl` module holds a single `Ptr[TlsSession]`
//     and stays backend-agnostic (a future OpenSSL backend is a shim swap);
//   * does the function-pointer wiring (set_bio with net_send/recv, conf_rng,
//     drbg_seed) in C, where it is expressible.
//
// Compiled with the C compiler against the vendored mbedTLS headers (see
// tpyc/build/mbedtls.py), in its own TU -- so mbedTLS's macros never reach a
// TPy-generated TU. Declared to TPy via runtime/cpp/include/tpy/stdlib/
// mbedtls_h.hpp; never include that facade here.
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include <mbedtls/ssl.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/x509_crt.h>
#include <mbedtls/pk.h>
#include <mbedtls/net_sockets.h>
#include <mbedtls/error.h>

struct tpy_tls_session {
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context drbg;
    mbedtls_x509_crt cacert;    // trusted roots (client verify)
    mbedtls_x509_crt owncert;   // presented cert (server / future client mTLS)
    mbedtls_pk_context ownkey;
    mbedtls_net_context net;    // wraps the socket fd (we do NOT own it)
    int has_cacert;
    int has_owncert;
};

typedef struct tpy_tls_session tpy_tls_session;

// TPy passes strings as a (view pointer, length) pair that is NOT
// NUL-terminated (see socket.py's tpy_resolve_ipv4 convention); mbedTLS's
// path/hostname APIs want a NUL-terminated `const char*`, so copy into a
// temporary. Returns NULL on OOM; caller frees a non-NULL result.
static char *dup_cstr(const unsigned char *p, size_t len)
{
    char *out = (char *)malloc(len + 1);
    if (out == NULL) {
        return NULL;
    }
    if (len > 0) {
        memcpy(out, p, len);
    }
    out[len] = '\0';
    return out;
}

// Allocate + init every sub-object and seed the DRBG from the platform
// entropy source. Returns NULL on allocation/seed failure.
tpy_tls_session *tpy_tls_new(void)
{
    tpy_tls_session *s = (tpy_tls_session *)calloc(1, sizeof(*s));
    if (s == NULL) {
        return NULL;
    }
    mbedtls_ssl_init(&s->ssl);
    mbedtls_ssl_config_init(&s->conf);
    mbedtls_entropy_init(&s->entropy);
    mbedtls_ctr_drbg_init(&s->drbg);
    mbedtls_x509_crt_init(&s->cacert);
    mbedtls_x509_crt_init(&s->owncert);
    mbedtls_pk_init(&s->ownkey);
    mbedtls_net_init(&s->net);
    s->net.fd = -1;

    static const char pers[] = "tpy_tls";
    int rc = mbedtls_ctr_drbg_seed(&s->drbg, mbedtls_entropy_func, &s->entropy,
                                   (const unsigned char *)pers, sizeof(pers) - 1);
    if (rc != 0) {
        // Free what we initialized; the fd is -1 so net_free is a no-op.
        mbedtls_ssl_free(&s->ssl);
        mbedtls_ssl_config_free(&s->conf);
        mbedtls_x509_crt_free(&s->cacert);
        mbedtls_x509_crt_free(&s->owncert);
        mbedtls_pk_free(&s->ownkey);
        mbedtls_ctr_drbg_free(&s->drbg);
        mbedtls_entropy_free(&s->entropy);
        free(s);
        return NULL;
    }
    return s;
}

void tpy_tls_free(tpy_tls_session *s)
{
    if (s == NULL) {
        return;
    }
    // We do not own the fd (the TPy socket does); detach before teardown so
    // mbedtls_net_free does not close a descriptor the socket still owns.
    s->net.fd = -1;
    mbedtls_ssl_free(&s->ssl);
    mbedtls_ssl_config_free(&s->conf);
    mbedtls_x509_crt_free(&s->cacert);
    mbedtls_x509_crt_free(&s->owncert);
    mbedtls_pk_free(&s->ownkey);
    mbedtls_ctr_drbg_free(&s->drbg);
    mbedtls_entropy_free(&s->entropy);
    free(s);
}

// Client config: verifying secure-by-default. `ca_path` (PEM/DER file) is the
// trust store; NULL means no roots loaded (only valid with verify=0). When
// verify is nonzero, authmode is REQUIRED so a bad chain fails the handshake.
int tpy_tls_config_client(tpy_tls_session *s, const unsigned char *ca_path,
                          size_t ca_path_len, int verify)
{
    int rc = mbedtls_ssl_config_defaults(&s->conf, MBEDTLS_SSL_IS_CLIENT,
                                         MBEDTLS_SSL_TRANSPORT_STREAM,
                                         MBEDTLS_SSL_PRESET_DEFAULT);
    if (rc != 0) {
        return rc;
    }
    mbedtls_ssl_conf_rng(&s->conf, mbedtls_ctr_drbg_random, &s->drbg);
    if (ca_path_len > 0) {
        char *path = dup_cstr(ca_path, ca_path_len);
        if (path == NULL) {
            return MBEDTLS_ERR_X509_ALLOC_FAILED;
        }
        rc = mbedtls_x509_crt_parse_file(&s->cacert, path);
        free(path);
        if (rc != 0) {
            return rc;
        }
        s->has_cacert = 1;
        mbedtls_ssl_conf_ca_chain(&s->conf, &s->cacert, NULL);
    }
    mbedtls_ssl_conf_authmode(&s->conf, verify ? MBEDTLS_SSL_VERIFY_REQUIRED
                                               : MBEDTLS_SSL_VERIFY_NONE);
    return 0;
}

// Server config (test peer / future server-side TLS): present cert+key files.
int tpy_tls_config_server(tpy_tls_session *s,
                          const unsigned char *cert_path, size_t cert_path_len,
                          const unsigned char *key_path, size_t key_path_len)
{
    int rc = mbedtls_ssl_config_defaults(&s->conf, MBEDTLS_SSL_IS_SERVER,
                                         MBEDTLS_SSL_TRANSPORT_STREAM,
                                         MBEDTLS_SSL_PRESET_DEFAULT);
    if (rc != 0) {
        return rc;
    }
    mbedtls_ssl_conf_rng(&s->conf, mbedtls_ctr_drbg_random, &s->drbg);

    char *cpath = dup_cstr(cert_path, cert_path_len);
    char *kpath = dup_cstr(key_path, key_path_len);
    if (cpath == NULL || kpath == NULL) {
        free(cpath);
        free(kpath);
        return MBEDTLS_ERR_X509_ALLOC_FAILED;
    }
    rc = mbedtls_x509_crt_parse_file(&s->owncert, cpath);
    if (rc == 0) {
        rc = mbedtls_pk_parse_keyfile(&s->ownkey, kpath, NULL,
                                      mbedtls_ctr_drbg_random, &s->drbg);
    }
    free(cpath);
    free(kpath);
    if (rc != 0) {
        return rc;
    }
    s->has_owncert = 1;
    return mbedtls_ssl_conf_own_cert(&s->conf, &s->owncert, &s->ownkey);
}

// Bind the config into the ssl context (after config_*).
int tpy_tls_setup(tpy_tls_session *s)
{
    return mbedtls_ssl_setup(&s->ssl, &s->conf);
}

// Point the session's BIO at an existing socket fd (we do not take ownership).
void tpy_tls_set_fd(tpy_tls_session *s, int fd)
{
    s->net.fd = fd;
    mbedtls_ssl_set_bio(&s->ssl, &s->net, mbedtls_net_send, mbedtls_net_recv,
                        NULL);
}

// SNI + the name verified against the peer cert's CN/SAN.
int tpy_tls_set_hostname(tpy_tls_session *s, const unsigned char *host,
                         size_t host_len)
{
    char *name = dup_cstr(host, host_len);
    if (name == NULL) {
        return MBEDTLS_ERR_SSL_ALLOC_FAILED;
    }
    int rc = mbedtls_ssl_set_hostname(&s->ssl, name);  // mbedTLS copies it
    free(name);
    return rc;
}

int tpy_tls_handshake(tpy_tls_session *s)
{
    return mbedtls_ssl_handshake(&s->ssl);
}

int tpy_tls_read(tpy_tls_session *s, unsigned char *buf, size_t len)
{
    return mbedtls_ssl_read(&s->ssl, buf, len);
}

int tpy_tls_write(tpy_tls_session *s, const unsigned char *buf, size_t len)
{
    return mbedtls_ssl_write(&s->ssl, buf, len);
}

uint32_t tpy_tls_verify_result(tpy_tls_session *s)
{
    return mbedtls_ssl_get_verify_result(&s->ssl);
}

const char *tpy_tls_version(tpy_tls_session *s)
{
    return mbedtls_ssl_get_version(&s->ssl);
}

int tpy_tls_close_notify(tpy_tls_session *s)
{
    return mbedtls_ssl_close_notify(&s->ssl);
}

// Classify a handshake/read/write return code so the TPy layer never needs
// the raw MBEDTLS_ERR_* macro values:
//   0 ok (>= 0, e.g. a byte count)   1 WANT_READ        2 WANT_WRITE
//   3 peer close_notify (clean EOF)  4 cert verify failed
//   5 timeout                        6 other error (use tpy_tls_strerror)
int tpy_tls_classify(int rc)
{
    if (rc >= 0) {
        return 0;
    }
    switch (rc) {
    case MBEDTLS_ERR_SSL_WANT_READ:
        return 1;
    case MBEDTLS_ERR_SSL_WANT_WRITE:
        return 2;
    case MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY:
        return 3;
    case MBEDTLS_ERR_X509_CERT_VERIFY_FAILED:
        return 4;
    case MBEDTLS_ERR_SSL_TIMEOUT:
        return 5;
    default:
        return 6;
    }
}

// Render an mbedTLS error code into buf (NUL-terminated) for diagnostics.
// buf is unsigned char* to match the TPy Ptr[UInt8] binding.
void tpy_tls_strerror(int code, unsigned char *buf, size_t len)
{
    mbedtls_strerror(code, (char *)buf, len);
}
