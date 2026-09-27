# A float list local rebound to a comprehension of ints is refused: the local's
# inferred type hints the comprehension but does not convert its ints.
from tpy import int32


def rebind(xs: list[float], ints: list[int32]) -> None:
    ys = xs
    ys = [i for i in ints]  # tpyc: error(/'ys' is bound to float elements at line 7 and to int32 elements here/)
    print(ys)


rebind([1.5], [3])
