# functools.reduce -- 3-arg form only (see lib/tpy/functools.py for the
# 2-arg form blocker). Covers lambda and named-function callables, cross-type
# T/U (summing str lengths into Int32), the empty-input + initial case that
# just returns the seed, and a reference-type accumulator (list[Int32]) that
# verifies copy(initial) actually copies rather than aliasing the caller's
# seed.
from functools import reduce
from tpy import Int32

def add(a: Int32, b: Int32) -> Int32:
    return a + b

def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4, 5]

    # Named function
    print(reduce(add, xs, Int32(0)))       # 15
    print(reduce(add, xs, Int32(100)))     # 115

    # Lambda
    print(reduce(lambda a, b: a * b, xs, Int32(1)))   # 120
    print(reduce(lambda a, b: max(a, b), xs, Int32(0)))  # 5

    # Empty list + initial returns the seed unchanged
    empty: list[Int32] = []
    print(reduce(add, empty, Int32(42)))   # 42

    # Cross-type T != U: U=Int32 accumulator, T=str element
    words: list[str] = ["hi", "hello", "world"]
    total_len = reduce(lambda acc, w: acc + Int32(len(w)), words, Int32(0))
    print(total_len)   # 12

    # Reference-type accumulator: reduce must copy(initial) so the caller's
    # seed is not aliased into the return value.
    init: list[Int32] = [100]
    nums: list[Int32] = [1, 2, 3]
    built = reduce(lambda acc, x: acc + [x], nums, init)
    print(built)   # [100, 1, 2, 3]
    print(init)    # [100] -- seed untouched

main()
