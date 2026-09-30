# urllib.parse: urlsplit/urlparse components, the netloc-derived
# .hostname/.port/.username/.password accessors, geturl(), urlunsplit/urlunparse.
from urllib.parse import urlsplit, urlparse, urlunsplit, urlunparse


def show(u: str) -> None:
    p = urlsplit(u)
    print(p.scheme + " | " + p.netloc + " | " + p.path + " | " + p.query
          + " | " + p.fragment)


def main() -> None:
    show("http://192.0.2.10:8002/api/tables?fmt=json#sec")
    show("https://user:pw@example.com/a/b")
    show("/relative/only?x=1")
    show("//netloc-only/p")
    show("HTTPS://CAPS.Example.COM/Path")
    show("")

    full = urlsplit("http://user:secret@Host.Example:8002/p?q=1#f")
    host = full.hostname
    print("hostname=" + (host if host is not None else "None"))
    port = full.port
    if port is None:
        print("port=None")
    else:
        print("port=", port)
    user = full.username
    print("user=" + (user if user is not None else "None"))
    pw = full.password
    print("pass=" + (pw if pw is not None else "None"))
    print("geturl=" + full.geturl())

    # No userinfo / no port -> None.
    bare = urlsplit("http://plainhost/p")
    print("bare-port-none=", bare.port is None)
    print("bare-user-none=", bare.username is None)

    # urlparse splits the legacy ;params segment off the last path element.
    pr = urlparse("http://h/a/b;type=d?q=1#f")
    print("params=" + pr.params + " path=" + pr.path)
    print("rebuilt=" + pr.geturl())

    print(urlunsplit(("http", "h:9", "/p", "a=b", "frag")))
    print(urlunparse(("http", "h", "/p", "k=v", "q=1", "f")))

    # IPv6 bracketed host; unbalanced brackets raise ValueError (CPython parity).
    v6 = urlsplit("http://[::1]:9000/v6")
    vh = v6.hostname
    print("v6=" + (vh if vh is not None else "None"))
    try:
        bad = urlsplit("http://[::1/x")
        print("no-raise " + bad.netloc)
    except ValueError:
        print("ipv6-ValueError")

    # Non-numeric port raises ValueError (CPython parity).
    try:
        bp = urlsplit("http://h:zz/x").port
        print("no-raise port")
    except ValueError:
        print("port-ValueError")

    # Leading whitespace lstripped, interior tab removed, trailing kept.
    print("[" + urlsplit("  http://h/a\tb/p  ").path + "]")


main()
