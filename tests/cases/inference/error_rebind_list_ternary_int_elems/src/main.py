# A float list local rebound to a ternary of int list literals is refused: the
# local's inferred type hints the arms but converts none of their ints.


def pick(xs: list[float], c: bool) -> None:
    ys = xs
    ys = [1] if c else [2]  # tpyc: error(/'ys' is bound to float elements at line 6 and to int32 elements here/)
    print(ys)


pick([1.5], True)
