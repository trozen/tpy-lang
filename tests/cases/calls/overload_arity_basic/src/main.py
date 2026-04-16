# @overload stubs with different arities dispatching to one impl with defaults
from typing import overload


@overload
def log(x: float) -> float: ...  # tpyc: ok

@overload
def log(x: float, base: float) -> float: ...  # tpyc: ok

def log(x: float, base: float | None = None) -> float:
    if base is None:
        return x
    return x / base


def main() -> None:
    print(log(16.0))
    print(log(16.0, 2.0))


main()
