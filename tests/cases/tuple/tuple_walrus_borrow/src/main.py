# A walrus-bound pointer-repr tuple takes the borrow form like a regular
# VarDecl: the binding aliases its captured member, and a storage-form
# source (list element) is lifted element-wise so mutation reaches it.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def literal_capture() -> None:
    b = Box(5)
    print((t := (1, b))[0])
    t[1].val = 99
    print(b.val)


def storage_source() -> None:
    items: list[tuple[Int32, Box]] = [(2, Box(7))]
    print((t := items[0])[0])
    t[1].val = 42
    print(items[0][1].val)


def main() -> None:
    literal_capture()
    storage_source()


main()
