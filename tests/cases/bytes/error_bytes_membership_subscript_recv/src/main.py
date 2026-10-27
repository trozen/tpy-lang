# A bytes haystack the bytes membership arm does not lower (a subscript
# read) stays refused: the sequence-containment helper is not its fallback.
class H:
    bs: list[bytes]

    def __init__(self) -> None:
        self.bs = [b"ab", b"cd"]


def has(h: H) -> bool:
    return 99 in h.bs[1]  # tpyc: error(/binop.shape.in/)


def main() -> None:
    print(has(H()))


main()
