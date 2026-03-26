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

main()
