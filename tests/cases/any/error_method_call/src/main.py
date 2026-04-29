# Method calls on raw Any are rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = "hello"
    print(a.upper())  # tpyc: error(/[Cc]annot call method.*on type Any/)


main()
