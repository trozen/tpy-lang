# bytes sibling: a bytes tuple-unpack target reassigned in a loop owns its
# element (a span into the per-iteration tuple temp would dangle).
def split_b(b: bytes) -> tuple[bytes, bytes]:
    n = len(b) // 2
    return (b[:n], b[n:])


def main() -> None:
    head = b"abcdefghijklmnop"
    acc = 0
    while len(head) > 1:
        head, tail = split_b(head)
        acc = acc + len(tail)
    print(acc)
    print(len(head))


main()
