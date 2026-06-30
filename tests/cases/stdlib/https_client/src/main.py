# http.client.HTTPSConnection over real TLS, exercised via the _tls injection
# seam (same offline pattern as http_client's conn.sock=): a handshaken
# ssl.SSLSocket is moved into conn._tls, so request()/getresponse() run the
# full HTTP/1.1 flow through mbedtls_ssl_read/write instead of a bare socket.
# SSLSocket is @nocopy, so the move into the Optional field is a real move (a
# silent copy would be a compile error). The peer is ssl._wrap_server over the
# other end of a socketpair; it reads the encrypted request and replies with a
# Content-Length response, which getresponse() parses through makefile().
# no_cpython: needs a real mbedTLS handshake + internal server peer (no CPython
# equivalent without a real server/threads), as in ssl/tls_handshake.
from typing import Final
from tpy import Own
import socket
import ssl
from ssl import SSLSocket
import http.client
from http.client import HTTPSConnection

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

CERT_PATH: Final[str] = "/tmp/tpy_test_https_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_https_key.pem"


def write_fixtures() -> None:
    with open(CERT_PATH, "w") as f:
        f.write(CERT_PEM)
    with open(KEY_PATH, "w") as f:
        f.write(KEY_PEM)


def handshaken_pair() -> tuple[Own[SSLSocket], Own[SSLSocket]]:
    """A verified client SSLSocket + its server peer, handshake complete."""
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(CERT_PATH)
    cli = ctx.wrap_socket(a, "localhost", False)
    srv = ssl._wrap_server(b, CERT_PATH, KEY_PATH)
    i = 0
    cdone = False
    sdone = False
    while i < 500 and not (cdone and sdone):
        if not sdone and srv.do_handshake():
            sdone = True
        if not cdone and cli.do_handshake():
            cdone = True
        i += 1
    cli.setblocking(True)
    srv.setblocking(True)
    return cli, srv


def main() -> None:
    write_fixtures()
    cli, srv = handshaken_pair()

    # Construct with an explicit context to exercise the copy(context) branch
    # of __init__ (context=None takes the create_default_context branch). Port
    # is passed positionally on purpose: a `context=` kwarg that skips the
    # defaulted `port` trips a cross-module default-constant bug (BUGS.md).
    # Then inject the handshaken socket via the offline _tls seam.
    conn = HTTPSConnection("localhost", http.client.HTTPS_PORT, None,
                           ssl.create_default_context())
    conn._tls = cli

    conn.request("GET", "/v1/resource")
    req = srv.recv(4096).decode()
    print("req-line:", req.split("\r\n")[0])
    print("has-host:", "Host: localhost" in req)

    srv.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 10\r\n\r\nabcdefghij")
    srv.close()

    resp = conn.getresponse()
    print(resp.status, resp.reason, resp.version)
    print(resp.getheader("content-type"))
    print(resp.read().decode())
    conn.close()


main()
