# Error: `**expr` into a generic function without `**kwargs` is rejected by
# the argument binding, as at the non-generic `error_kwargs_star_unpack`.
# CPython unpacks the dict into the named parameters: a restriction TPy
# does not document yet (BUGS.md#unpack-into-named-params-rejects).
from typing import TypedDict


class TD(TypedDict):
    a: int


def f[T](x: T, a: int = 5) -> T:
    print(a)
    return x


def main() -> None:
    d = TD(a=7)
    f(1, **d)  # tpyc: error(/'f' does not accept \*\*kwargs/)


main()
