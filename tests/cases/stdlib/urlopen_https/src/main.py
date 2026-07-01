# urlopen over HTTPS: an https URL routes to HTTPSConnection and reads the
# response over a real SSLSocket, driven through the internal `_urlopen` with a
# pre-connected Box[_Connection] (the offline seam). Single-threaded: the server
# pre-sends its response before _urlopen runs and stays open so the client's TLS
# write lands; the response (Rc-shared session) outlives the dropped connection.
# no_cpython: needs a real mbedTLS handshake + internal server peer.
from typing import Final
from tpy import Own
from tplib import Box
import socket
import ssl
from ssl import SSLSocket
from urllib.request import _urlopen
from http.client import HTTPSConnection, _Connection


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
    srv.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nsecure")

    conn: Box[_Connection] = Box(HTTPSConnection("das.test", 443, None, None, cli))
    resp = _urlopen("https://das.test/health", None, None, None, conn)
    print(resp.status, resp.reason)
    print(resp.read())
    srv.close()


main()
