# A nullable tuple return takes its bare twin's declared-return rule: a
# @readonly method handing out a field at a mutable element is refused.
from tpy import int32, readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    k: int32
    item: Box

    def __init__(self, k: int32) -> None:
        self.k = k
        self.item = Box(k)

    @readonly
    def peek(self, k: bool) -> tuple[Box, int32] | None:
        if k:
            return None
        return (self.item, self.k)  # tpyc: error(/Cannot return readonly\[Box\] as tuple element 0 at mutable return type 'tuple\[Box, int32\] \| None'; declare the return as 'tuple\[readonly\[Box\], int32\] \| None'/)


def main() -> None:
    h = Holder(1)
    r = h.peek(False)
    if r is not None:
        print(r[0].n)


main()
