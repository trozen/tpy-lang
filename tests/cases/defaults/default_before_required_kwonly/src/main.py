# A defaulted parameter followed by a REQUIRED keyword-only one is valid
# Python but has no C++ default-argument spelling (defaults must be
# trailing), so the default is dropped from the signature and materialized
# at the call site instead.
from tpy import Int64


def f(a: Int64, b: Int64 = 10, *, c: Int64) -> Int64:
    return a * 100 + b * 10 + c


def g(a: Int64, b: Int64 = 1, c: Int64 = 2, *, d: Int64, e: Int64 = 5) -> Int64:
    return a * 10000 + b * 1000 + c * 100 + d * 10 + e


def main() -> None:
    print(f(1, c=3))
    print(f(1, 2, c=3))

    print(g(1, d=4))
    print(g(1, 7, d=4))
    print(g(1, 7, 8, d=4, e=9))


main()
