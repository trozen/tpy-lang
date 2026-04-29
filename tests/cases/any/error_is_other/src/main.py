# `is` on Any is allowed only against None. Other RHS values are rejected.

from typing import Any


def main() -> None:
    a: Any = 1
    if a is 5:  # tpyc: error(/(only supported with None|is.*not.*Any|is.*compare)/)
        print("yes")


main()
