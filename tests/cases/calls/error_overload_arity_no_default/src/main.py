# Error: short-arity stub omits an impl param that has no default
from typing import overload


@overload
def f(x: int) -> int: ...  # tpyc: error(/no default/)

@overload
def f(x: int, y: int) -> int: ...

def f(x: int, y: int) -> int:
    return x + y


def main() -> None:
    print(f(1))


main()
