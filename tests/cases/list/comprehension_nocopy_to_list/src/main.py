# A fixed-length comprehension of a @nocopy element resolves to list, not a
# stack Array: the Array build (default-construct + index-assign) can't hold a
# non-copyable element. @nocopy makes a silent copy a compile error.
from tpy import Int32, nocopy


@nocopy
class Handle:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def main() -> None:
    xs = [Handle(i) for i in range(4)]  # tpyc: type(/list\[Handle\]/)
    total = 0
    for h in xs:
        total += h.v
    print(total)


main()
