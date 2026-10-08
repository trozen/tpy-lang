# An unmarked nullable tuple result of an @auto_readonly method is marked per
# element like its bare twin, not followed as a whole: returning own storage
# at the unmarked element is rejected in the const clone, naming the marker.
from tpy import int32, auto_readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    k: int32
    item: Box

    def __init__(self, k: int32) -> None:
        self.k = k
        self.item = Box(k)

    @auto_readonly
    def maybe(self, k: bool) -> tuple[Box, int32] | None:
        if k:
            return None
        return (self.item, self.k)  # tpyc: error(/Cannot return readonly\[Box\] as tuple element 0 at a mutable part of return type 'tuple\[Box, int32\] \| None' of an @auto_readonly method; mark that part 'auto_readonly\[Box\]'/)


def main() -> None:
    h = Holder(1)
    t = h.maybe(False)
    if t is not None:
        t[0].n += 1
    print(h.item.n)


main()
