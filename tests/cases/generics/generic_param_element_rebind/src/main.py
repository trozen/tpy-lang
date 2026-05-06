# Regression: generic function `def f[T](a: list[T])` with a local borrow
# `result: T = a[0]` followed by element-rebind `result = a[i]`. Sema must
# mark `a` as mutated so codegen emits non-const `std::vector<T>&` (matching
# the non-const `T*` for the rebound local). Three independent guardrails
# enforce this -- regressing any one of them previously caused the param
# to drop to `const std::vector<T>&` while the local stayed `T*`, failing
# C++ compile with "invalid conversion from 'const T*' to 'T*'".
from tpy import Int32


def last[T](a: list[T]) -> T:
    result: T = a[0]
    for i in range(1, len(a)):
        result = a[i]
    return result


def main() -> None:
    nums: list[Int32] = [10, 20, 30, 40]
    print(last(nums))

    strs: list[str] = ["a", "b", "c"]
    print(last(strs))


main()
