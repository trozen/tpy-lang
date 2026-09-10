# Bundled CA store: create_default_context() trusts the vendored Mozilla root
# bundle out of the box (no load_verify_locations). Asserts the bundle is
# embedded + non-empty, and that a default context REJECTS a self-signed cert
# (which is not a Mozilla root) -- proving the store holds the real roots, not
# a trust-all placeholder. The self-signed cert's CN/SAN is localhost, so the
# only possible rejection reason is the untrusted chain, not a hostname
# mismatch. A real public-root handshake can't run offline, so this is the
# strongest offline proof. no_cpython (same reason as tls_handshake).
# Coverage limit: this can't distinguish "bundle wired into conf_ca_chain"
# from "bundle present but unwired" (a bare empty-trust SSLContext() also
# rejects the self-signed cert) -- proving the wiring needs a network-gated
# handshake against a real Mozilla-rooted host. See docs/SSL_DESIGN.md.
from typing import Final
import ssl
from ssl import SSLSocket  # qualified ssl.SSLSocket annotations are unsupported
import socket

CERT_PEM: Final[str] = """-----BEGIN CERTIFICATE-----
MIIBlTCCATugAwIBAgIUe2CartEUhHtqoCYrRV89NXxqz8EwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MDYyOTE1MDY1NloYDzIxMjYwNjA1
MTUwNjU2WjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAATi4r8fZOEM8tz66TgRALGG7z33xtTCAHavwkRqu8crpAaMoNVIsMxE
tP9yXT/7crk2Jpju9JqnkjzM/iLZ5gbqo2kwZzAdBgNVHQ4EFgQUcHp1/TdGBPiN
WGIQoSCKEgty4yUwHwYDVR0jBBgwFoAUcHp1/TdGBPiNWGIQoSCKEgty4yUwDwYD
VR0TAQH/BAUwAwEB/zAUBgNVHREEDTALgglsb2NhbGhvc3QwCgYIKoZIzj0EAwID
SAAwRQIgE8EzoNEb464cVe4PlS6BpNoBLmBWGkwUQ9mTi5JqX5UCIQCRCx3f+YQW
Ddslcyu0U0qfufOT/QbqMaDSyosTTmLteQ==
-----END CERTIFICATE-----
"""

KEY_PEM: Final[str] = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg2kn/USvpv4Ilspd2
xfLz4BM0UjqqhFJndB7QYY+ijAihRANCAATi4r8fZOEM8tz66TgRALGG7z33xtTC
AHavwkRqu8crpAaMoNVIsMxEtP9yXT/7crk2Jpju9JqnkjzM/iLZ5gbq
-----END PRIVATE KEY-----
"""

CERT_PATH: Final[str] = "tpy_test_ca_cert.pem"
KEY_PATH: Final[str] = "tpy_test_ca_key.pem"


def write_fixtures() -> None:
    with open(CERT_PATH, "w") as f:
        f.write(CERT_PEM)
    with open(KEY_PATH, "w") as f:
        f.write(KEY_PEM)


def bundle_embedded() -> None:
    n = ssl._bundled_ca_count()
    # A pinned Mozilla root set carries ~100+ roots; >50 proves it parsed and
    # is substantial (not empty, not a single placeholder).
    print("bundle non-empty:", n > 50)


def default_context_rejects_self_signed() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()  # bundled roots only; NO load_verify_locations
    cli = ctx.wrap_socket(a, "localhost", False)
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    srv = sctx.wrap_socket(b, server_side=True, do_handshake_on_connect=False)

    sdone = False
    i = 0
    while i < 500:
        if not sdone:
            try:
                if srv.do_handshake():
                    sdone = True
            except ssl.SSLError:
                break  # server aborts after the client rejects the cert
        try:
            if cli.do_handshake():
                print("FAIL: self-signed cert was accepted")
                return
        except ssl.SSLCertVerificationError:
            print("default context rejected self-signed cert")
            return
        i += 1
    print("FAIL: no verify decision")


def bare_context_trusts_nothing() -> None:
    # A bare SSLContext() (no create_default_context, no load_verify_locations)
    # keeps CERT_REQUIRED but starts with an EMPTY trust store -- so it rejects
    # the peer. Guards that __init__ does not load the bundle by default (only
    # create_default_context() flips _use_bundled_ca). With zero trusted CAs
    # mbedTLS aborts the handshake before per-cert verification (a generic
    # SSLError, not SSLCertVerificationError), so catch the base class.
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.SSLContext()
    cli = ctx.wrap_socket(a, "localhost", False)
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    srv = sctx.wrap_socket(b, server_side=True, do_handshake_on_connect=False)

    sdone = False
    i = 0
    while i < 500:
        if not sdone:
            try:
                if srv.do_handshake():
                    sdone = True
            except ssl.SSLError:
                break
        try:
            if cli.do_handshake():
                print("FAIL: bare context accepted untrusted cert")
                return
        except ssl.SSLError:
            print("bare context rejected untrusted cert")
            return
        i += 1
    print("FAIL: no verify decision")


def main() -> None:
    write_fixtures()
    bundle_embedded()
    default_context_rejects_self_signed()
    bare_context_trusts_nothing()


main()
