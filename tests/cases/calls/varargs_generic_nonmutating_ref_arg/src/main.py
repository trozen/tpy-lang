# Generic vararg with T inferred from a non-value ref-typed arg. The variadic
# inference branch strips Ref from the arg side before binding T (mirroring
# how the non-variadic ref-param match works), so T binds to Box, not Ref[Box]
# -- the latter would render as illegal `varargs<Box&>`.
from tpy import int32, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def count[T](*items: T) -> int32:
    return len(items)


def via_param(b: Box, c: Box) -> int32:
    return count(b, c)  # tpyc: ok


def main() -> None:
    x = Box(3)
    y = Box(4)
    print(via_param(x, y))


main()
