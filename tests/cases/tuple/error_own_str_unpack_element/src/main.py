# An `Own[str]` element at a standalone unpack target: only Own[record] moves are
# mirrored, so the unpack rejects.
from tpy import Int32, Own


def use() -> Int32:
    s, n = mk()  # tpyc: error(/stmt.tuple_unpack/)
    print(s)
    return n


def mk() -> tuple[Own[str], Int32]:
    return ("x", 1)


def main() -> None:
    print(use())


main()
