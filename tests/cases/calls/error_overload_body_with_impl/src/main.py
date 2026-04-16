# Error: bodied @overload cannot be paired with a trailing implementation
from typing import overload


@overload
def f(x: int) -> int:  # tpyc: error(/body and cannot be paired/)
    return x + 1

@overload
def f(x: str) -> int:
    return len(x)

def f(x: int | str) -> int:
    return 0


def main() -> None:
    print(f(1))


main()
