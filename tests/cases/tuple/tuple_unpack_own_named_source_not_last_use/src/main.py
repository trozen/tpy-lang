# Guard against the named-owned-source move over-firing: when the tuple local
# is read AGAIN after the unpack, the unpack is NOT its last use, so the source
# must be COPIED (`auto __tup = t`), not moved, and stays usable for the later
# read. The guard is the generated-code SNAPSHOT: a spurious `std::move(t)`
# would flip `main.cpp` to `auto&& __tup = std::move(t)` and fail the comp
# phase. Runtime output can't distinguish copy from move here -- the element is
# necessarily copyable (a @nocopy source reused after the unpack would be a
# build error on the correct copy path), and moving a copyable Box leaves its
# int field intact -- so the snapshot is the real check, not the 3/3 output.
from tpy import Own, Int32


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def make() -> tuple[Own[Box], Own[Box]]:
    return (Box(1), Box(2))


def main() -> None:
    t = make()
    a, b = t
    print(a.val + b.val)
    print(t[0].val + t[1].val)


main()
