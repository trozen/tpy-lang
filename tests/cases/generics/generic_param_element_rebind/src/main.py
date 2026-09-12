# Regression: generic function `def f[T](a: list[T])` with a local borrow
# `result: T = a[0]` followed by element-rebind `result = a[i]`. Sema must
# mark `a` as mutated so codegen emits non-const `std::vector<T>&` (matching
# the non-const `T*` for the rebound local). Three independent guardrails
# enforce this -- regressing any one of them previously caused the param
# to drop to `const std::vector<T>&` while the local stayed `T*`, failing
# C++ compile with "invalid conversion from 'const T*' to 'T*'".
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def last[T](a: list[T]) -> T:
    result: T = a[0]
    for i in range(1, len(a)):
        result = a[i]
    return result


def main() -> None:
    nums: list[int32] = [10, 20, 30, 40]
    print(last(nums))

    strs: list[str] = ["a", "b", "c"]
    print(last(strs))

    # A REFERENCE-type T: the return aliases the last element rather than
    # copying it, which the value-typed instantiations above cannot show.
    # 99 proves the alias; a copy would leave ps[2] at 30.
    ps: list[Point] = [Point(10), Point(20), Point(30)]
    r = last(ps)
    r.x = 99
    print(ps[2].x)


main()
