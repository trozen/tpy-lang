# A NESTED class constructor called with keyword arguments has no lowering: the
# top-level spelling reaches lowering with its kwargs already resolved into
# positional order, and the nested one does not, so the arm has nothing to
# render from. The positional spelling of the same call is pinned by
# tests/cases/records/nested_class.
from tpy import int32


class Outer:
    class Inner:
        v: int32

        def __init__(self, v: int32, w: int32 = 0) -> None:
            self.v = v + w


def f() -> int32:
    ok = Outer.Inner(1, 2)
    kw = Outer.Inner(v=1)           # tpyc: error(/expr\.method_call/)
    return ok.v + kw.v


def main() -> None:
    print(f())


main()
