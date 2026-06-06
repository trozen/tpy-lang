# A borrow-form tuple local with a PARAM-rooted member returned as a
# per-element-Own tuple: the Own slot receives a COPY of the member -- the
# storage conversion must never MOVE through the borrow pointer and gut the
# caller's object. b reading 5 after the call is the regression lock (a
# destructive move would leave it moved-from/empty). The copy being SILENT
# is a separate tracked gap (BUGS.md: the direct literal form `return
# (b, 0)` already requires explicit copy(); the bound-name form should too).
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Own[Box], Int32]:
    pair = (b, 0)
    return pair


def main() -> None:
    b = Box(5)
    got, n = f(b)
    print(b.val)
    print(got.val)
    print(n)


main()
