# requests.get over HTTPS: an https URL routes to HTTPSConnection behind the
# Session's Box[_Connection] seam and dispatches virtually over a real SSLSocket.
# Single-threaded: the server pre-sends its response before requests.get runs
# (otherwise the atomic request+getresponse deadlocks against an idle peer), and
# stays open so the client's TLS write lands; Content-Length frames the reply.
# no_cpython: needs a real mbedTLS handshake + internal server peer.
from typing import Final
from tpy import Own
from tplib import Box
import socket
import ssl
from ssl import SSLSocket
from http.client import HTTPSConnection
import tplib.requests as requests
from json import JsonValue


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

CERT_PATH: Final[str] = "tpy_test_requests_https_cert.pem"
KEY_PATH: Final[str] = "tpy_test_requests_https_key.pem"


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
    srv.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 13\r\n\r\n{\"rows\": 42}\n")

    s = requests.Session()
    conn = HTTPSConnection("das.test", 443, None, None)
    conn._tls = cli
    s._connection = Box(conn)
    r = s.get("https://das.test/v1/tables", {"db": "das"})
    print(r.status_code, r.ok, r.reason)
    print(r.headers["Content-Type"])
    d = r.json()
    if isinstance(d, dict):
        v: JsonValue = d["rows"]
        if isinstance(v, int):
            print("rows =", v)
    srv.close()


main()
