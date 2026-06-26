# A read timeout surfaces as requests.Timeout (a RequestException), not a raw
# OSError. Driven over a socketpair whose peer never answers; the timeout is set
# directly on the injected socket (the connection seam bypasses
# create_connection), so getresponse's makefile-backed read times out and
# _request_on re-raises it as Timeout. The second attempt confirms a timeout is
# also catchable as the RequestException base.
import socket
from http.client import HTTPConnection
import tplib.requests as requests
from tplib.requests import Timeout, RequestException


def _hang_request() -> None:
    a, b = socket.socketpair()
    a.settimeout(0.05)
    conn = HTTPConnection("api.test", 8002)
    conn.sock = a
    s = requests.Session()
    s.connection = conn
    s.get("http://api.test:8002/hang")
    b.close()


def main() -> None:
    try:
        _hang_request()
        print("NO TIMEOUT")
    except Timeout as e:
        print("timeout msg ok:", "timed out" in str(e))

    # The same timeout is catchable as the RequestException base class.
    try:
        _hang_request()
        print("NO TIMEOUT")
    except RequestException as e:
        print("caught as base:", "timed out" in str(e))


main()
