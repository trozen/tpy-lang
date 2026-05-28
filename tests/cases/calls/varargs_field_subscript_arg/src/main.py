# Lvalue regression for the ref-element vararg pack: TpyFieldAccess (`self.a`)
# and TpySubscript (`items[i]`) args should keep the direct `&arg` form, not
# go through the rvalue-temp-hoist path. @nocopy Box makes this a tight guard
# -- a regression that wrongly hoisted these would emit `Box __tmp = p.a;`
# which attempts a copy of a non-copyable type and fails the C++ build.
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


class Pair:
    a: Box
    b: Box

    def __init__(self, x: Int32, y: Int32) -> None:
        self.a = Box(x)
        self.b = Box(y)


def sum_all(*items: Box) -> Int32:
    n: Int32 = 0
    for it in items:
        n += it.val
    return n


def via_field(p: Pair) -> Int32:
    return sum_all(p.a, p.b)  # tpyc: ok


def via_subscript(items: list[Box]) -> Int32:
    return sum_all(items[0], items[1])  # tpyc: ok


def main() -> None:
    p = Pair(3, 4)
    print(via_field(p))
    lst: list[Box] = []
    lst.append(Box(5))
    lst.append(Box(6))
    print(via_subscript(lst))


main()
