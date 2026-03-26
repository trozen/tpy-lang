# Bytes literal construction and printing
def main() -> None:
    empty = b""
    hello = b"hello"
    binary = b"\x00\x01\xff"
    escape = b"\t\n\r\\"

    print(empty)
    print(hello)
    print(binary)
    print(escape)
    print(len(hello))
    print(len(binary))

main()
