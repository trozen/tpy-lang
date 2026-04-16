# Error: arity-variant stubs with an impl that uses *args
from typing import overload


@overload
def f(x: int) -> int: ...

@overload
def f(x: int, y: int) -> int: ...

def f(x: int, y: int = 0, *rest: int) -> int:  # tpyc: error(/\*args/)
    return x + y


def main() -> None:
    print(f(1))


main()
