# == / != on Any dispatches through the equals slot. Same-typeid contents
# compare via their underlying ==; mismatched typeid returns False.

from typing import Any


def main() -> None:
    a: Any = 1
    b: Any = 1
    c: Any = "1"
    d: Any = 2
    print(a == b)
    print(a == c)
    print(a == d)
    print(a != b)
    print(a != c)


main()
