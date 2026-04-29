# Subscript on raw Any is rejected -- narrow first.

from typing import Any


def main() -> None:
    a: Any = [1, 2, 3]
    print(a[0])  # tpyc: error(/[Cc]annot index type Any/)


main()
