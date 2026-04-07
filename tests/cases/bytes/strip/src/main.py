# bytes.strip() strips ASCII whitespace (space, tab, newline, etc.)
def main() -> None:
    padded = b"  hello  "
    print(padded.strip())
    print(padded.lstrip())
    print(padded.rstrip())

    tabs = b"\thello\n"
    print(tabs.strip())

    no_ws = b"hello"
    print(no_ws.strip())

    empty = b""
    print(empty.strip())

    only_ws = b"   "
    print(only_ws.strip())

    # BytesView (from slice) -- strip/lstrip/rstrip return views
    bv_data: bytes = b"  hi  "
    v = bv_data[0:6]
    print(len(v.strip()))
    print(len(v.lstrip()))
    print(len(v.rstrip()))

main()
