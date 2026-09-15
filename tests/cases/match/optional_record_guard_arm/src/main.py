# A `match` over an Optional-of-record whose arms the null-split PARTITION
# cannot render -- a guarded class arm, or a value-type record inner, which
# the partition's inner dispatch has no shape for -- falls back to the
# Optional chain tier instead of rejecting. Sections: guarded class arm, a
# guarded capture that aliases (mutation observed after the match), a
# ValueType record inner, method.
from typing import Optional

from tpy import Own, ValueType, int32


class Box:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


class Point(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def flagged(o: Optional[Box], flag: bool) -> str:
    match o:  # tpyc: ok
        case None:
            return "none"
        case Box() if flag:
            return "flag"
        case _:
            return "box"


def bump(o: Optional[Box], flag: bool) -> None:
    # The capture aliases the caller's Box -- the increment is visible there.
    match o:  # tpyc: ok
        case Box() as b if flag:
            b.val += 1
        case _:
            pass


def value_inner(o: Optional[Point]) -> str:
    # A value-type record inner has no partition inner dispatch at all.
    match o:  # tpyc: ok
        case None:
            return "none"
        case Point(x=1):
            return "one"
        case _:
            return "other"


class Holder:
    item: Box

    def __init__(self, item: Own[Box]) -> None:
        self.item = item

    def describe(self, flag: bool) -> str:
        match self.item:  # tpyc: ok
            case Box(val=1) if flag:
                return "one-flag"
            case Box():
                return "box"


def main() -> None:
    print("flagged:", flagged(None, True), flagged(Box(1), True),
          flagged(Box(1), False))
    shared = Box(1)
    bump(shared, True)
    bump(shared, False)
    print("bump:", shared.val)
    print("value_inner:", value_inner(None), value_inner(Point(1)),
          value_inner(Point(2)))
    print("method:", Holder(Box(1)).describe(True),
          Holder(Box(1)).describe(False))


main()
