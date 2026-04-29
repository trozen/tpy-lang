# Binary arithmetic on raw Any is rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = 1
    print(a + 1)  # tpyc: error(/Invalid operand types/)


main()
