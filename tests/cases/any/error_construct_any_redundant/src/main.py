# Any(x) where x is already Any is rejected as redundant -- pass x
# directly.

from typing import Any


def main() -> None:
    a: Any = 1
    b = Any(a)  # tpyc: error(/already Any is redundant/)
    print(b)


main()
