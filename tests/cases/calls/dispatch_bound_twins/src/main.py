# A @dispatch group whose bodyless variants differ only in a type parameter's
# bound (ValueType / ReferenceType) beside a third callable-bearing shape:
# the twins count as one shape when the resolver picks its regime, and every
# variant still competes -- the bound picks per argument.
# Bodyless @cpp_template variants have no CPython body, hence no_cpython.
from typing import Callable
from tpy import dispatch, ValueType, ReferenceType
from tpy.extern import cpp_template


class P:
    def __init__(self, v: int) -> None:
        self.v = v


@dispatch
@cpp_template("({1})({0})")
def f[T: ValueType](x: T, k: Callable[[T], int]) -> int: ...


@dispatch
@cpp_template("({1})({0})")
def f[T: ReferenceType](x: T, k: Callable[[T], int]) -> int: ...


@dispatch
@cpp_template("({1})({0})")
def f(x: str, k: Callable[[str], int]) -> int: ...


def main() -> None:
    p = P(3)
    # the reference twin, the value twin and the third shape, per argument
    print(f(p, lambda q: q.v))  # tpyc: ok
    print(f(4, lambda q: q + 1))  # tpyc: ok
    print(f("ab", lambda s: len(s)))  # tpyc: ok


main()
