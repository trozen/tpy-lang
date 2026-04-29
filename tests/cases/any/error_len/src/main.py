# len() on raw Any is rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = [1, 2, 3]
    print(len(a))  # tpyc: error(/No matching overload for len/)


main()
