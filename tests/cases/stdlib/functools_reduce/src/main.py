# functools.reduce -- 3-arg form. Covers lambda and named-function callables,
# cross-type T/U (summing str lengths into Int32), the empty-input + initial
# case that just returns the seed, and a reference-type accumulator
# (list[Int32]) that verifies copy(initial) actually copies rather than
# aliasing the caller's seed. Also exercises non-list iterables: literal
# list, range(), empty list literal -- unblocked by the
# list-literal-vs-Iterable[T] conformance work and the
# post-overload-resolution element-type coercion. The 2-arg form is in
# functools_reduce_2arg.
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

    # Iterable[T] inputs -- list literal and range() pass directly without
    # needing to bind to a typed local first.
    print(reduce(add, [1, 2, 3], 0))  # 6
    print(reduce(add, range(1, 5), 0))  # 10 (range(1,5) = 1+2+3+4)

    # str accumulator (U=str). Used to dangle silently because U was
    # auto-downgraded to StrView and the lambda's owned-string return
    # converted to a view of a temporary.
    parts: list[str] = ["foo", "bar", "baz"]
    print(reduce(lambda a, b: a + b, parts, ""))   # foobarbaz

main()
