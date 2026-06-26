"""A small curl-like HTTP client built on tplib.requests.

Fetches a URL and prints the response. Supports a request method, a request
body, repeatable custom headers, basic auth, a request timeout, response-header
inclusion, and writing the body to a file -- a focused subset of curl's surface.

Usage:
    uv run tpyc -x examples/net/curl.py -- http://example.com/
    uv run tpyc -x examples/net/curl.py -- -X POST -d '{"k":1}' \
        -H "Content-Type: application/json" http://api.test/items
    uv run tpyc -x examples/net/curl.py -- -i -u user:pass http://api.test/
    uv run tpyc -x examples/net/curl.py -- -m 5 http://api.test/slow
    uv run tpyc -x examples/net/curl.py -- -o out.bin http://api.test/blob

Plaintext HTTP only (tplib.requests has no TLS yet). This sandbox has no
outbound network -- run it on a host that can reach the target.
"""
import sys
from argparse import ArgumentParser
from tpy import Int32
import tplib.requests as requests
from tplib.requests import RequestException, Timeout


def _split_header(raw: str) -> tuple[str, str]:
    idx = raw.find(":")
    if idx < 0:
        return (raw.strip(), "")
    return (raw[:idx].strip(), raw[idx + 1:].strip())


def _split_user(raw: str) -> tuple[str, str]:
    idx = raw.find(":")
    if idx < 0:
        return (raw, "")
    return (raw[:idx], raw[idx + 1:])


def main() -> Int32:
    parser = ArgumentParser(description="Fetch a URL over HTTP (curl-like).")
    parser.add_argument("url", help="the URL to request")
    parser.add_argument("-X", "--request", default="",
                        help="HTTP method (default GET, or POST when -d is given)")
    parser.add_argument("-d", "--data", default="", help="request body")
    parser.add_argument("-H", "--header", action="append",
                        help="extra request header 'Name: Value' (repeatable)")
    parser.add_argument("-u", "--user", default="",
                        help="basic auth credentials 'user:password'")
    parser.add_argument("-o", "--output", default="",
                        help="write the body to a file instead of stdout")
    parser.add_argument("-i", "--include", action="store_true",
                        help="print the response headers before the body")
    parser.add_argument("-m", "--max-time", type=float, default=0.0,
                        help="abort the request after this many seconds (0 = no limit)")
    args = parser.parse_args()

    headers: dict[str, str] = {}
    hdr_list = args.header
    if hdr_list is not None:
        for h in hdr_list:
            kv = _split_header(h)
            headers[kv[0]] = kv[1]

    auth: tuple[str, str] | None = None
    if args.user != "":
        auth = _split_user(args.user)

    body: bytes | None = None
    if args.data != "":
        body = args.data.encode()

    method: str = args.request
    if method == "":
        method = "POST" if body is not None else "GET"

    # 0 (the default) means no limit; anything positive becomes the socket
    # timeout, so a stalled connect/read aborts instead of hanging forever.
    timeout: float | None = None
    if args.max_time > 0.0:
        timeout = args.max_time

    try:
        r = requests.request(method, args.url, None, body, None, headers, auth,
                             timeout)
    except Timeout:
        print(f"request timed out after {args.max_time}s")
        return 28          # curl's exit code for a timeout
    except RequestException:
        print("request failed")
        return 1

    print(f"{r.status_code} {r.reason}")
    if args.include:
        for kv in r.headers.items():
            print(f"{kv[0]}: {kv[1]}")
        print("")

    if args.output != "":
        f = open(args.output, "wb")
        f.write(r.content)
        f.close()
        print(f"wrote {len(r.content)} bytes to {args.output}")
    else:
        print(r.text)
    return 0


# sys.exit(main()) rather than a bare main() so the returned status (0 ok,
# 1 request failed, 28 timeout -- curl's codes) becomes the process exit code.
sys.exit(main())
