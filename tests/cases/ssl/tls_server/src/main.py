# Public server-side TLS: SSLContext().load_cert_chain(cert, key) +
# wrap_socket(sock, server_side=True) drives a real mbedTLS handshake as the
# SERVER against a verifying client over a socketpair (step-wise, single-
# threaded, no threads). Covers a full handshake + app-data exchange from the
# server's view, and the error path: server_side=True without load_cert_chain
# raises SSLError before any socket I/O. no_cpython: CPython's ssl needs a
# real server/threads here (same as ssl/tls_handshake).
from typing import Final
import ssl
from ssl import SSLSocket  # qualified ssl.SSLSocket annotations are unsupported
import socket


def tls_ver(v: str) -> str:
    # Version-robust: bundled mbedTLS 3.6 negotiates TLSv1.3, system 2.28 (the
    # mainstream-LTS branch) negotiates TLSv1.2. Both are a good modern
    # handshake, and the minor version is an mbedTLS property, not TPy's --
    # collapse it so output.txt matches under either --mbedtls mode.
    return "TLSv1.2+" if v in ("TLSv1.2", "TLSv1.3") else v


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

CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_server_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_ssl_server_key.pem"
BAD_CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_server_bad.pem"


def write_fixtures() -> None:
    with open(CERT_PATH, "w") as f:
        f.write(CERT_PEM)
    with open(KEY_PATH, "w") as f:
        f.write(KEY_PEM)


def drive(cli: SSLSocket, srv: SSLSocket) -> bool:
    """Alternate handshake steps until both sides complete (non-blocking)."""
    cdone = False
    sdone = False
    i = 0
    while i < 500 and not (cdone and sdone):
        if not sdone and srv.do_handshake():
            sdone = True
        if not cdone and cli.do_handshake():
            cdone = True
        i += 1
    return cdone and sdone


def server_roundtrip() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)

    cctx = ssl.create_default_context()
    cctx.load_verify_locations(CERT_PATH)
    cli = cctx.wrap_socket(a, "localhost", False)

    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    srv = sctx.wrap_socket(b, server_side=True, do_handshake_on_connect=False)

    if not drive(cli, srv):
        print("FAIL: handshake did not converge")
        return
    print("server handshake:", tls_ver(srv.version()))

    cli.setblocking(True)
    srv.setblocking(True)
    cli.sendall(b"ping from client")
    print("server recv:", srv.recv(64).decode())
    srv.sendall(b"pong from server")
    print("client recv:", cli.recv(64).decode())

    # Server close_notify -> the client's next recv is a clean EOF (b"").
    srv.close()
    print("client eof:", len(cli.recv(64)) == 0)
    cli.close()


def missing_cert_chain() -> None:
    # server_side=True without a prior load_cert_chain must raise, and before
    # any socket I/O (the config step fails first).
    a, b = socket.socketpair()
    sctx = ssl.SSLContext()
    try:
        srv = sctx.wrap_socket(b, server_side=True,
                               do_handshake_on_connect=False)
        print("FAIL: expected SSLError, got", srv.version())
    except ssl.SSLError:
        print("no cert chain rejected")


def malformed_cert() -> None:
    # A cert file that isn't valid PEM: tls_config_server fails -> SSLError.
    with open(BAD_CERT_PATH, "w") as f:
        f.write("not a certificate\n")
    a, b = socket.socketpair()
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(BAD_CERT_PATH, BAD_CERT_PATH)
    try:
        srv = sctx.wrap_socket(b, server_side=True,
                               do_handshake_on_connect=False)
        print("FAIL: expected SSLError, got", srv.version())
    except ssl.SSLError:
        print("malformed cert rejected")


def server_hostname_rejected() -> None:
    # server_hostname is client-only; passing it with server_side=True raises
    # (CPython raises ValueError; TPy has no ValueError base -> SSLError).
    a, b = socket.socketpair()
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    try:
        srv = sctx.wrap_socket(b, "localhost", False, True)
        print("FAIL: expected SSLError, got", srv.version())
    except ssl.SSLError:
        print("server_hostname with server_side rejected")


def main() -> None:
    write_fixtures()
    server_roundtrip()
    missing_cert_chain()
    malformed_cert()
    server_hostname_rejected()


main()
