# A float local rebound to a generic call whose T only the local's type would
# pick for the argument is refused: first([a]) returns the int a.
from tpy import int32


def first[T](v: list[T]) -> T:
    return v[0]


def pick(a: int32) -> None:
    y = 0.5
    y = first([a])  # tpyc: error(/'y' is bound to float at line 11 and to int32 here/)
    print(y)


pick(3)
