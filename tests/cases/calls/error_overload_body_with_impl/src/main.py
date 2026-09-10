# Error: a @overload stub declares a signature and cannot carry a body (that is @dispatch)
from typing import overload


@overload
def f(x: int) -> int:  # tpyc: error(/has a body.*use @dispatch/)
    return x + 1

@overload
def f(x: str) -> int:
    return len(x)

def f(x: int | str) -> int:
    return 0


def main() -> None:
    print(f(1))


main()
