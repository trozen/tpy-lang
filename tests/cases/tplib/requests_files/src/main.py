# tplib.requests: files= builds a multipart/form-data body from FileField
# parts; each carries a Content-Type (application/octet-stream by default, or
# the one given). A dict data= folds in as plain form parts alongside the
# files; a data=bytes body is ignored when files= is non-empty. An empty
# files={} is falsy so it does NOT force a multipart body (data= handling
# applies). A field name / filename with a `"` or CR/LF is percent-escaped. A
# 307 redirect forwards the multipart body to the next hop. The fixed boundary
# makes the sent bytes deterministic; inspected via a socketpair seam.
import socket
from http.client import HTTPConnection
from tplib import Box
import tplib.requests as requests
from tplib.requests import FileField


def send(data: bytes | dict[str, str] | None,
         files: dict[str, FileField]) -> None:
    a, b = socket.socketpair()
    b.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    conn = HTTPConnection("api.test", 80)
    conn.sock = a
    s._connection = Box(conn)
    r = s.post("http://api.test/upload", data, files=files)
    print(r.status_code)
    print(b.recv(65536))
    b.close()


def send_redirect(files: dict[str, FileField]) -> None:
    # 307 preserves method + body, so the multipart survives to the second hop.
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    b.sendall(b"HTTP/1.1 307 Temporary Redirect\r\n"
              b"Location: /next\r\nContent-Length: 0\r\n\r\n")
    d.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    s = requests.Session()
    s.headers = {"User-Agent": "test-agent"}
    h0 = HTTPConnection("api.test", 80)
    h0.sock = a
    s._connection = Box(h0)
    h1 = HTTPConnection("api.test", 80)
    h1.sock = c
    s._redirect_connections = [Box(h1)]
    r = s.post("http://api.test/submit", None, files=files)
    print(r.status_code, r.url)
    b.recv(65536)
    print(d.recv(65536))
    b.close()
    d.close()


def main() -> None:
    # default content type (octet-stream)
    send(None, {"doc": FileField("a.txt", b"hello")})
    # dict data folds in as a form part; explicit content type on the file
    send({"caption": "hi"}, {"doc": FileField("a.txt", b"hello"),
                             "pic": FileField("logo.png", b"img", "image/png")})
    # data=bytes is ignored when files= is non-empty (only a dict data folds in)
    send(b"ignored-bytes", {"doc": FileField("a.txt", b"hello")})
    # empty files={} is falsy: no multipart, data= handling applies (urlencoded)
    no_files: dict[str, FileField] = {}
    send({"field": "v"}, no_files)
    # a `"` and CR/LF in name/filename are percent-escaped
    send(None, {"na\"me": FileField("re\r\nport.txt", b"x")})
    # 307 redirect forwards the multipart body to the next hop
    send_redirect({"doc": FileField("a.txt", b"hello")})


main()
