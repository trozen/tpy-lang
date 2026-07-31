# Omitting BOTH a positional-or-keyword default and the keyword-only defaults
# after it must bind each its own default. TPy models defaults as C++
# positional defaults, so the keyword-only default used to slide into the
# skipped positional's slot (b saw c's 20).
from tpy import Int64


def combine(a: Int64, b: Int64 = 10, *, c: Int64 = 20) -> Int64:
    return a * 10000 + b * 100 + c


def partial(a: Int64, b: Int64 = 10, c: Int64 = 30, *, d: Int64 = 40) -> Int64:
    return a * 1000000 + b * 10000 + c * 100 + d


def main() -> None:
    print(combine(1))
    print(combine(1, 2))
    print(combine(1, c=3))
    print(combine(1, 2, c=3))

    print(partial(5))
    print(partial(5, d=9))
    print(partial(5, 6, d=9))


main()
