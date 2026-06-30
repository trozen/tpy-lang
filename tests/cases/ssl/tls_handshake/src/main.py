# ssl module end-to-end over a socketpair (single-threaded, step-wise, no
# threads): SSLContext.wrap_socket (verifying client) + the internal
# _wrap_server peer. Covers cert+hostname verify (happy), send/sendall,
# recv-EOF on close_notify, a hostname mismatch rejected, and CERT_NONE.
# Self-signed localhost cert+key embedded + written at runtime (a committed
# fixture). no_cpython: CPython's ssl needs a real server/threads here.
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

CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_ssl_key.pem"


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


def handshake_ok() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(CERT_PATH)
    cli = ctx.wrap_socket(a, "localhost", False)
    srv = ssl._wrap_server(b, CERT_PATH, KEY_PATH)
    if not drive(cli, srv):
        print("FAIL: handshake did not converge")
        return
    print("handshake:", cli.version())

    cli.setblocking(True)
    srv.setblocking(True)
    cli.sendall(b"hello tls")
    print("server got:", srv.recv(32).decode())
    print("sent:", cli.send(b"AB"))
    print("server got2:", srv.recv(32).decode())

    # close() sends close_notify -> the peer's next recv is a clean EOF (b"").
    cli.close()
    print("server eof:", len(srv.recv(32)) == 0)
    srv.close()


def hostname_mismatch() -> None:
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(CERT_PATH)
    cli = ctx.wrap_socket(a, "wronghost.example", False)
    srv = ssl._wrap_server(b, CERT_PATH, KEY_PATH)

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
                print("FAIL: expected certificate verify error")
                return
        except ssl.SSLCertVerificationError:
            print("verify rejected mismatched hostname")
            return
        i += 1
    print("FAIL: no verify decision")


def cert_none() -> None:
    # CERT_NONE: no chain/hostname verification -- the handshake completes
    # even with no trusted CA and no server_hostname.
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    ctx.verify_mode = ssl.CERT_NONE
    ctx.check_hostname = False
    cli = ctx.wrap_socket(a, "", False)
    srv = ssl._wrap_server(b, CERT_PATH, KEY_PATH)
    if drive(cli, srv):
        print("no-verify handshake:", cli.version())
    else:
        print("FAIL: no-verify handshake did not converge")


def main() -> None:
    write_fixtures()
    handshake_ok()
    hostname_mismatch()
    cert_none()


main()
