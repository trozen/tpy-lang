# Implicit dual-overload for `__deref__` with a reference-typed return.
# Without IMPLICIT_AUTO_READONLY_METHODS, this would compile to a single
# const __deref__() and mutation through the wrapper (`r.x = 99`) would be
# rejected by C++ as "assignment of member in read-only object". Tests that
# the implicit path produces both mutable and const overloads so field
# mutation through the wrapper works.
from tpy import int32, copy


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Ref:
    _target: Point

    def __init__(self, target: Point) -> None:
        self._target = copy(target)

    # Plain `def __deref__` -- no @auto_readonly or @readonly. Implicit path
    # should produce dual mutable/const overloads because the return type is
    # a reference type (Point).
    def __deref__(self) -> Point:
        return self._target


def read_only(r: Ref) -> int32:
    # Const path: Ref is a borrow; __deref__() const overload is used.
    return r.x + r.y


def main() -> None:
    pt: Point = Point(10, 20)
    r: Ref = Ref(pt)

    # Read through mutable __deref__.
    print(r.x)            # 10
    print(r.y)            # 20

    # Mutate through mutable __deref__ -- requires the non-const overload.
    r.x = int32(99)
    r.y = int32(88)
    print(r.x)            # 99
    print(r.y)            # 88

    # Borrow path goes through the const overload.
    print(read_only(r))   # 187


main()
