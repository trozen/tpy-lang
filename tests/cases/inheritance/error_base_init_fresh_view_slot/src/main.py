# A fresh value at a base VIEW parameter is rejected: the view would outlive
# its temporary (BUGS.md#base-init-args-separate-lowering).
from tpy import int32, Own, Span, readonly


def make(n: int32) -> Own[list[int32]]:
    return [n, n, n]


class Viewer:
    sp: Span[readonly[int32]]

    def __init__(self, sp: Span[readonly[int32]]) -> None:
        self.sp = sp


class Child(Viewer):
    # The ctor reject reports the enclosing `def` line, not the offending
    # call, so the annotation sits here.
    def __init__(self, n: int32) -> None:  # tpyc: error(/not yet supported.*ctor.base_init/)
        super().__init__(make(n))


def main() -> None:
    c = Child(7)
    print(c.sp[0])


main()
