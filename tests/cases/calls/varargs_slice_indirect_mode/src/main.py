# `args[1:]` on a non-value *args populated via individual args (`f(a, b, c)`)
# -- exercises indirect-mode storage (`T* const*`), which the generic
# `list_slice` template can't handle (it expects contiguous T storage).
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sum_tail(*items: Box) -> Int32:  # tpyc: ok
    n: Int32 = 0
    for b in items[1:]:
        n += b.val
    return n


def main() -> None:
    print(sum_tail(Box(1), Box(2), Box(3)))
    print(sum_tail(Box(10)))  # single arg -- tail is empty
    print(sum_tail())  # empty -- tail is also empty


main()
