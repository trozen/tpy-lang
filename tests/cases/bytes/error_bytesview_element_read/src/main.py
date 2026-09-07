# Reading an element out of a `list[BytesView]`: a view element leaves the
# static-storage question open, so the element family gate rejects.
from tpy import BytesView, Int32


def first_len(parts: list[BytesView]) -> Int32:
    x = parts[0]  # tpyc: error(/elem.bytes/)
    return len(x)


def main() -> None:
    print(first_len([b"ab"]))


main()
