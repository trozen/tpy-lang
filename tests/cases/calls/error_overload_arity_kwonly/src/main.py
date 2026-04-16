# Error: arity-variant stubs with an impl that uses keyword-only params
from typing import overload


@overload
def f(x: int) -> int: ...

@overload
def f(x: int, y: int) -> int: ...

def f(x: int, y: int = 0, *, verbose: bool = False) -> int:  # tpyc: error(/keyword-only/)
    if verbose:
        return x + y + 100
    return x + y


def main() -> None:
    print(f(1))


main()
