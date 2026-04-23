# Regression: `@nocopy` + `__del__` held as a field, initialized via a
# `@staticmethod` factory returning `Own[Self]`. This is the lib/tpy/re.py
# Pattern shape and depends on three compiler behaviors staying in sync:
#   * enclosing record's field assignment MIL-hoists (RHS has no body-local)
#     so the field is move-constructed, not default-inited-then-move-assigned;
#   * @nocopy+__del__ records have their auto default ctor suppressed (no
#     silent UB path if something slips past the MIL-hoist);
#   * Own[Self] factory return bypasses the dangling-return check that would
#     reject a `staticmethod -> Ptr[T]` shape.
# Confirms `__del__` fires exactly once, including after the Holder is moved
# into a consuming function -- the drop-flag ensures the move source does not
# retrigger the destructor on scope exit.
from __future__ import annotations
from tpy import Int32, Own, nocopy


@nocopy
class Resource:
    id: Int32
    def __init__(self, id: Int32) -> None:
        self.id = id
    def __del__(self) -> None:
        print("drop", self.id)

    @staticmethod
    def make(seed: Int32) -> Own[Resource]:
        base = seed + Int32(100)
        return Resource(base)


class Holder:
    _r: Resource
    tag: Int32

    def __init__(self, seed: Int32, tag: Int32) -> None:
        self._r = Resource.make(seed)
        self.tag = tag


def take(h: Own[Holder]) -> None:
    print("taken", h._r.id, "tag", h.tag)


def main() -> None:
    h = Holder(Int32(1), Int32(42))
    print("held", h._r.id, "tag", h.tag)
    take(h)
    print("after take")


main()
