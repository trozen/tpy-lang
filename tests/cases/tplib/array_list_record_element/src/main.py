# ArrayList[T, N] holding a RECORD element: the borrow-returning __getitem__
# binds a T& alias into the list, and __setitem__'s Own[T] value slot accepts a
# moved local or a constructed rvalue.
#
# The two are exercised on the same list but never at the same time, and that
# separation is deliberate rather than incidental: every alias below dies with
# the function that took it, so no write ever lands under a live borrow. That
# pairing is NOT safe today and this case must not be read as saying it is --
# `p = lst[0]` followed by `lst[0] = Point(..)` in one scope writes through the
# alias with no diagnostic (BUGS.md#setitem-write-under-live-element-borrow).
from tpy import int32
from tplib import ArrayList


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def show(lst: ArrayList[Point, 8]) -> None:
    for i in range(len(lst)):
        pt = lst[i]  # binds the element itself (const Point&), never a copy
        print(pt.x, pt.y)


def bump(lst: ArrayList[Point, 8]) -> None:
    p = lst[0]  # a mutable Point& alias into the list...
    p.x = 99  # ... so this write is visible through the list below
    # `p` ends here; the replace_* writes below run with no borrow live.


def replace_move(lst: ArrayList[Point, 8]) -> None:
    z = Point(7, 7)
    lst[1] = z  # last use of z -> moved into the Own[T] value slot


def replace_rvalue(lst: ArrayList[Point, 8]) -> None:
    lst[1] = Point(8, 8)  # a ctor prvalue binds the same slot directly


def main() -> None:
    lst = ArrayList[Point, 8]()
    lst.append(Point(1, 2))
    lst.append(Point(3, 4))
    bump(lst)
    show(lst)
    replace_move(lst)
    show(lst)
    replace_rvalue(lst)
    show(lst)


main()
