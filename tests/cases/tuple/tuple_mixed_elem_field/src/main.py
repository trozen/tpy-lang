# A tuple MIXING a pointer-repr Optional element with a plain reference
# element: the storage write from a borrow-form source dispatches per DEST
# slot shape (null-checked optional vs deref-copy value) in one
# tuple_to_storage converter. Copy semantics at the field boundary are
# intended (TPy fields own their tuple elements; the copy warnings assert
# that contract), so no post-write mutation check -- CPython would alias.
from typing import Optional
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class H:
    t: tuple[Optional[Box], Box]

    def __init__(self, a: Box, b: Box) -> None:
        self.t = (a, b)  # tpyc: warning(/copies/) warning(/copies/)

    def set(self, p: tuple[Optional[Box], Box]) -> None:
        self.t = p  # tpyc: warning(/copies/) warning(/copies/)


def main() -> None:
    a = Box(1)
    b = Box(2)
    h = H(a, b)
    x = Box(9)
    y = Box(8)
    h.set((x, y))
    first = h.t[0]
    if first is not None:
        print(first.val)
    print(h.t[1].val)
    # None in the optional slot survives the storage write.
    n: Optional[Box] = None
    h.set((n, y))
    print(h.t[0] is None)


main()
