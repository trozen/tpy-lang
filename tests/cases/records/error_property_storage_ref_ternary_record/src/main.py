# One of the reject tags the STORAGE-REF `@property` family raises: a
# getter whose return is a pointer-repr `Optional[T]` (or a ptr-variant
# union) hands back the FIELD's storage BY REFERENCE, a form no plain
# method returns, so the positions that were never taught the lift are
# located rejects rather than renders. The family, the fallback tag
# `expr.storage_ref_getter` (which no position reaches today) and the
# workaround are in docs/PROPERTY_DESIGN.md; TODO.md, "Four positions
# REJECT a storage-ref `@property` read where a lift would render it",
# holds the design, blocked on BUGS.md#getter-source-const-not-tracked.
# THIS CASE: both arms of a ternary, over a RECORD getter -- the
# reference-returning flavour, refused for the const reason rather
# than the storage-optional one.
from typing import Optional
from tpy import int32


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 1

    def bump(self) -> None:
        self.x += 1


class Src:
    _o: Optional[Rec]
    _r: Rec

    def __init__(self) -> None:
        self._o = Rec()
        self._r = Rec()

    # a getter whose return is a pointer-repr Optional hands back the FIELD's
    # `std::optional<Rec>` BY REFERENCE -- the storage-ref convention, which
    # no plain method can return
    @property
    def o(self) -> Optional[Rec]:
        return self._o

    @property
    def r(self) -> Rec:
        return self._r


def main() -> None:
    s = Src()
    c = True
    v = s.r if c else s.r  # tpyc: error(/ifexpr.getter_arm_const_source/)
    print(v.x)


main()
