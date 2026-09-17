# A bare-`T` @property getter at an `Optional[Rec]` instantiation, read into
# a local. The emitter spells every open-`T` return through `val_or_ref_t<T>`
# (`val_or_cref_t<T>` on the const twin), while the value-category rule
# `property_access_returns_cpp_ref` subtracts Optional, Union and protocol and
# answers "by value" at this substitution -- the one place the two disagree.
# The read never lowers, which is what keeps them agreeing everywhere the
# compiler actually asks; the plain-METHOD twin of the getter takes the same
# reject, so this is not a property-specific hole.
from tpy import Own, int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Cell[T]:
    _v: T

    def __init__(self, v: Own[T]) -> None:
        self._v = v

    @property
    def payload(self) -> T:
        return self._v


def use(c: Cell[Rec | None]) -> None:
    p = c.payload  # tpyc: error(/method\.record\.payload/)
    if p is not None:
        print(p.n)


def main() -> None:
    c = Cell[Rec | None](Rec(3))
    use(c)


main()
