# Calling a raw Any is rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = "hello"
    a()  # tpyc: error(/(narrow first|cannot call|not callable)/)


main()
