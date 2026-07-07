# SSLSocket.makefile() -- a buffered binary reader over a shared TLS session.
# The reader holds an Rc clone of the @nocopy _SslSession (session handle +
# socket), so the connection's fd is kept alive by the reader independently
# of the SSLSocket; a silent copy of the session would be a compile error
# (@nocopy). Drives the handshake step-wise over a socketpair with the
# internal server peer, then reads a multi-line payload through readline()
# and read() -- including reads issued AFTER the SSLSocket is closed, proving
# the shared session/fd outlives close() (deferred to the last Rc holder) --
# and confirms a clean close_notify surfaces as EOF (b"").
# no_cpython: same as ssl/tls_handshake (needs a real server/threads).
from typing import Final
import ssl
from ssl import SSLSocket
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

CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_mk_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_ssl_mk_key.pem"


def write_fixtures() -> None:
    with open(CERT_PATH, "w") as f:
        f.write(CERT_PEM)
    with open(KEY_PATH, "w") as f:
        f.write(KEY_PEM)


def drive(cli: SSLSocket, srv: SSLSocket) -> bool:
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

    cli.setblocking(True)
    srv.setblocking(True)

    # Server sends a multi-line payload then a clean close_notify (all of it
    # lands in the kernel buffer; this stays single-threaded).
    srv.sendall(b"first line\nsecond line\ntail without newline")
    srv.close()

    reader = cli.makefile()
    print("l1:", reader.readline().decode().rstrip())

    # Close the SSLSocket while the reader is still open. The session + fd are
    # shared via Rc, so close() sends the client close_notify but defers the fd
    # close to the last holder -- the reader keeps the connection alive and can
    # still drain the buffered payload. (If close() eager-closed the fd, the
    # reads below would fail; this is the deferred-close soundness guard.)
    cli.close()
    print("l2:", reader.readline().decode().rstrip())
    rest = reader.read()
    print("rest:", rest.decode())
    print("eof:", len(reader.read()) == 0)
    reader.close()


main()
