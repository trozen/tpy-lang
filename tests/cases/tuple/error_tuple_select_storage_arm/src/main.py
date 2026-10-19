# A select of a borrow tuple and a storage-form tuple local is refused for
# now (BUGS.md#tuple-select-deferred-shapes).
from tpy import int32


class A:
    def __init__(self, s: str) -> None:
        self.s = s


def show(a: A) -> None:
    print(a.s)


def g(x: tuple[A, int32], flag: bool) -> None:
    w = (A("inner"), 7)
    v = x if flag else w  # tpyc: error(/decl\.slot_type/)
    show(v[0])


g((A("outer"), 1), False)
