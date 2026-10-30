# A `StrView` local rebound by an unpack from a temporary tuple would view
# storage that dies with the statement: refused, as its twin
# `a = mk(i + 1)[0]` is.
from tpy import int32, StrView


def mk(n: int32) -> tuple[str, int32]:
    return ("long string materialization " * n, n)


def f() -> None:
    a: StrView = "initial"
    k = 0
    for i in range(2):
        a, k = mk(i + 1)  # tpyc: error(/Cannot bind StrView 'a' to a temporary view source/)
    print(a, k)


f()
