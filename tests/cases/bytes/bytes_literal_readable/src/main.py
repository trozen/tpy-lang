# Bytes literals render readably in generated C++ (printable ASCII raw,
# control/high bytes as \xNN, greedy-\x break) in borrow and storage positions.


def take(x: bytes) -> int:
    return len(x)


def make_greeting() -> bytes:
    return b"hello"


def main() -> None:
    print(take(b"hello"))
    print(take(b""))

    g = make_greeting()
    print(len(g), g[0])

    quoted = b'a"b\\c'
    print(len(quoted), quoted[1])

    binary = b"\x00\x80\xff"
    print(len(binary), binary[0], binary[1], binary[2])

    greedy = b"\x1fab"
    print(len(greedy), greedy[0], greedy[1], greedy[2])

    # b"" lands in an owned (vector) element -- exercises the empty-owned slot
    lst: list[bytes] = [b"world", b"\x00\x80\xff", b""]
    print(len(lst), len(lst[0]), len(lst[1]), len(lst[2]))


main()
