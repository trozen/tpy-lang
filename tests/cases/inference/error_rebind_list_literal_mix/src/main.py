# A float-list local rebound to [int, float]: the literal drops the hint and
# its own element join refuses.
from tpy import int32


def rebind(xs: list[float], a: int32) -> None:
    ys = xs
    ys = [a, 2.5]  # tpyc: error(/List literal has mixed types: element 2 is float, but earlier elements are int32; CPython keeps each value's own type, so convert to one type: float\(\.\.\.\) on the int32 elements/)
    print(ys)


rebind([1.5], 3)
