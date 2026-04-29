# typing.cast() target must be a concrete type. Union targets are
# rejected -- typeid is a single-type concept and union dispatch requires
# narrowing first.

from typing import Any, cast


def f(x: Any) -> int | str:
    return cast(int | str, x)  # tpyc: error(/target must be a concrete type/)


def main() -> None:
    a: Any = 1
    print(f(a))


main()
