# A float-or-None local rebound to first([a]) is refused: the seeded
# `float | None` element hint converts no int through the None.
from tpy import int32


def first[T](v: list[T]) -> T:
    return v[0]


def rebind(c: bool, a: int32) -> None:
    y = 0.5 if c else None
    y = first([a])  # tpyc: error(/'y' is bound to float at line 11 and to int32 here/)
    print(y)


rebind(True, 3)
