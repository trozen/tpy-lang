# tplib.requests streaming: stream=True leaves the body unread and hands back a
# Response whose iter_content(chunk_size) pulls bytes lazily from the live
# HTTPResponse (backed by makefile's own dup'd fd). A streamed connection is
# NOT returned to the pool (its socket is mid-body). Covers a Content-Length
# body, a chunked transfer-encoding body, `.raw.read()`, context-manager close,
# an empty (Content-Length: 0) body, and iter_content on a NON-streamed response
# (chunks over the already-read .content, matching requests). The reader is a
# non-copyable HTTPResponse, so if the move into the Response ever silently
# became a copy it would be a compile error, not a silent aliasing bug.
# no_cpython: tplib.requests has no CPython module.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests


def stream_content_length() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/big", True)
    s._pool[key] = Box(conn)

    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\n0123456789ABCDEF")
    r = s.get("http://api.test/big", stream=True)
    print("status:", r.status_code)
    # A streamed connection is never pooled -- its socket is mid-response.
    print("not pooled:", key not in s._pool)
    got = bytearray()
    for chunk in r.iter_content(5):
        print("chunk:", chunk.decode())
        got += chunk
    print("full:", bytes(got).decode())
    b.close()


def stream_chunked() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/chunked", True)
    s._pool[key] = Box(conn)

    # Two chunks (5 + 6 bytes) then the terminating 0-length chunk.
    b.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
              b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
    r = s.get("http://api.test/chunked", stream=True)
    got = bytearray()
    for chunk in r.iter_content(8):
        got += chunk
    print("chunked:", bytes(got).decode())
    b.close()


def stream_raw() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/raw", True)
    s._pool[key] = Box(conn)

    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nraw payload")
    r = s.get("http://api.test/raw", stream=True)
    raw = r.raw
    if raw is not None:
        print("raw.read:", raw.read(-1).decode())
    else:
        print("raw missing")
    b.close()


def stream_context_manager() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/ctx", True)
    s._pool[key] = Box(conn)

    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nabcd")
    # Bind r first, then use it as a context manager: `with s.get(...) as r:`
    # miscompiles when a union-typed default arg (verify) needs a temp in the
    # with-header (BUGS.md).
    r = s.get("http://api.test/ctx", stream=True)
    # got declared outside the with-block: a with-block-scoped bytearray is
    # stored optional-form and rejects +=, same shape as a try-scoped local.
    got = bytearray()
    with r:
        for chunk in r.iter_content(2):
            got += chunk
        print("ctx body:", bytes(got).decode())
    # After the with-block, close() released the reader.
    print("raw after close:", r.raw is None)
    b.close()


def stream_empty_body() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/empty", True)
    s._pool[key] = Box(conn)

    # A Content-Length: 0 body: iter_content's first read hits EOF immediately,
    # so the loop yields nothing.
    b.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
    r = s.get("http://api.test/empty", stream=True)
    chunks = 0
    for chunk in r.iter_content(8):
        chunks += 1
    print("empty chunks:", chunks)
    b.close()


def non_streamed_iter_content() -> None:
    a, b = socket.socketpair()
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    key = requests._pool_key("http://api.test/full", True)
    s._pool[key] = Box(conn)

    # Without stream=True the body is read into .content; iter_content still
    # works, chunking over the already-read content (matching requests).
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n0123456789")
    r = s.get("http://api.test/full")
    got = bytearray()
    for chunk in r.iter_content(4):
        got += chunk
    print("non-stream iter_content:", bytes(got).decode())
    print("content intact:", r.content.decode())
    b.close()


def main() -> None:
    stream_content_length()
    stream_chunked()
    stream_raw()
    stream_context_manager()
    stream_empty_body()
    non_streamed_iter_content()


main()
