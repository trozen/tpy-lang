# requests follows an http -> https redirect: hop 0 (plaintext) returns a 302 to
# an https Location, hop 1 runs through an injected pre-handshaken SSLSocket (the
# _tls seam). Single-threaded: the TLS server pre-sends its response before
# requests.get runs (else the atomic request+getresponse deadlocks against an
# idle peer) and stays open so the client's TLS write lands.
# no_cpython: needs a real mbedTLS handshake + internal server peer.
from typing import Final
from tpy import Own
from tplib import Box
import socket
import ssl
from ssl import SSLSocket
from http.client import HTTPConnection, HTTPSConnection
import tplib.requests as requests


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
    sctx = ssl.SSLContext()
    sctx.load_cert_chain(CERT_PATH, KEY_PATH)
    srv = sctx.wrap_socket(b, server_side=True, do_handshake_on_connect=False)
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
    # Pre-encrypt + buffer the hop-1 (https) response before requests.get runs.
    srv.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\nlogged.")

    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 302 Found\r\n"
              b"Location: https://secure.test/login\r\n"
              b"Content-Length: 0\r\n\r\n")

    s = requests.Session()
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPSConnection("secure.test", 443, None, None)
    h1._tls = cli
    s._redirect_connections = [Box(h1)]
    r = s.get("http://api.test/start")
    print(r.status_code, r.text)
    print(r.url)
    print(len(r.history))
    b.close()
    srv.close()


main()
