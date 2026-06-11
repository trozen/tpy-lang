# Constructing a generic record from a subscript of a list of a REFERENCE type:
# the element borrow form (Ref[R]) is canonicalized to storage form, so the
# Own[T] field stores an owned copy, inferring Owned[R] (not Owned[Ref[R]]).
# Forces the value-vs-reference distinction: mutating the source after
# construction leaves the stored copy unchanged. Parity-checked -- the cpy
# copy() stub deepcopies, so CPython agrees the copy is independent.
from tpy import Own, copy


class R:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Owned[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


def grab[T](src: list[T]) -> Own[Owned[T]]:
    o = Owned(copy(src[0]))  # tpyc: type(/Owned\[T\]/)
    return o


def main() -> None:
    items: list[R] = [R(1)]
    held = grab(items)
    items[0].n = 99
    print(held.v.n)
    print(items[0].n)


main()
