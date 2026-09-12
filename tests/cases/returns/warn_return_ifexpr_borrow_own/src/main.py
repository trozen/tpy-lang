# A TERNARY of two borrow-returning calls at an `Own[T]` return: whichever arm
# runs hands back a borrow of the caller's storage, so the owning slot copies
# and warns like the single-call spelling
# (warn_return_record_borrow_method_own). The lvalue-ternary twin at a BORROW
# return stays legal (return_container_ifexpr_borrow). The copy is the
# ACKNOWLEDGED CPython divergence, so the case prints only what both agree on
# -- `updated()` mutates through the borrow BEFORE the return, which both
# sides see.
from tpy import int32, Own, copy


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def updated(self) -> 'Point':
        self.x += 1
        return self


def pick(a: Point, b: Point, c: bool) -> Own[Point]:
    return a.updated() if c else b.updated()  # tpyc: warning(/copies Point into owned storage/)


def pick_copy(a: Point, b: Point, c: bool) -> Own[Point]:
    return copy(a.updated() if c else b.updated())  # tpyc: ok


def main() -> None:
    a = Point(1)
    b = Point(2)
    print(pick(a, b, True).x, a.x)
    print(pick_copy(a, b, False).x, b.x)


main()
