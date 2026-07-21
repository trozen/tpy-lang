# Bare key-iteration over a narrowed-Optional dict param: the `is not None`
# guard, the key loop, and a callee mutation visible to the caller (the param
# aliases the caller's dict, no copy). Keys are longer than the SSO buffer so
# a view into a dead temporary reads garbage instead of accidentally passing.


def scan(d: dict[str, str] | None) -> int:
    n = 0
    if d is not None:
        for k in d:
            if k.lower() == "transfer-encoding-extension":
                n += 1
        d["seen-by-the-key-iteration-scan"] = "yes"
    return n


def main() -> None:
    headers = {"Transfer-Encoding-Extension": "chunked", "X-Short": "1"}
    print(scan(headers))
    print(scan(None))
    print(headers["seen-by-the-key-iteration-scan"])


main()
