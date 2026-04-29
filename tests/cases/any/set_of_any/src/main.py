# set[Any] is allowed: each element's hash slot is consulted at insert.
# Hashable contents (int, str) succeed.

from typing import Any


def main() -> None:
    s: set[Any] = set()
    a: Any = 1
    b: Any = "hello"
    s.add(a)
    s.add(b)
    print(len(s))


main()
