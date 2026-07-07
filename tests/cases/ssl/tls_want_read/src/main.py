# ssl SSLWant* subclasses on a non-blocking socket: recv with no data pending
# raises SSLWantReadError (an SSLError subclass, so an `except ssl.SSLError`
# handler still catches it); flooding an unread socketpair raises
# SSLWantWriteError. SSLZeroReturnError is exercised as a catch arm only:
# recv on close_notify returns b"" (CPython SSLSocket.recv parity), so the
# raise path (close_notify surfacing mid-write) has no deterministic
# socketpair trigger.
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

CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_want_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_ssl_want_key.pem"


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


def main() -> None:
    write_fixtures()
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(CERT_PATH)
    cli = ctx.wrap_socket(a, "localhost", False)
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    srv = sctx.wrap_socket(b, server_side=True, do_handshake_on_connect=False)
    if not drive(cli, srv):
        print("FAIL: handshake did not converge")
        return
    print("handshake:", cli.version())

    # No data pending on the non-blocking socket -> SSLWantReadError.
    try:
        cli.recv(32)
        print("FAIL: expected SSLWantReadError")
    except ssl.SSLWantReadError:
        print("want-read raised")

    # Subclass relation: the same condition is caught by the SSLError base.
    try:
        cli.recv(32)
        print("FAIL: expected SSLError")
    except ssl.SSLError:
        print("want-read caught as SSLError")

    # SSLZeroReturnError arm: srv has no data pending either, so this recv
    # raises SSLWantReadError -- the zero-return arm does not match and the
    # SSLError base arm takes it. Proves the class is declared and catchable.
    try:
        srv.recv(32)
        print("FAIL: expected SSLError")
    except ssl.SSLZeroReturnError:
        print("FAIL: unexpected zero-return")
    except ssl.SSLError:
        print("srv want-read")

    # Flood the unread socketpair until the kernel buffer fills: the BIO send
    # hits EAGAIN and mbedTLS reports WANT_WRITE -> SSLWantWriteError. Stop at
    # the first raise (a WANT_WRITE mid-record needs a same-buffer retry; this
    # session is only closed afterwards).
    chunk = b"x" * 16384
    i = 0
    filled = False
    while i < 10000 and not filled:
        try:
            cli.send(chunk)
        except ssl.SSLWantWriteError:
            print("want-write raised")
            filled = True
        i += 1
    if not filled:
        print("FAIL: socket buffer never filled")

    cli.close()
    srv.close()


main()
