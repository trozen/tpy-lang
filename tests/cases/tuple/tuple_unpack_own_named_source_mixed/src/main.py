# Sibling of tuple_unpack_own_named_source: a MIXED owned/value tuple
# (tuple[Own[A], Int32]) unpacked from a named local at its last use. The owned
# element moves out of the moved source; the value element is read. @nocopy
# forces the owned slot to be a move -- a silent copy would be a build error.
from tpy import Own, nocopy, Int32


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def make() -> tuple[Own[Box], Int32]:
    return (Box(5), 7)


def consume(b: Own[Box]) -> Int32:
    b.val += 1
    return b.val


def main() -> None:
    t = make()
    box, n = t
    print(consume(box) + n)


main()
