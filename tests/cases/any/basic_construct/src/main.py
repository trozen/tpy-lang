# Construct Any from various source types. Universal ops on Any (print,
# str, ...) land in phase 4 -- this case verifies only the storage path.

from typing import Any


def main() -> None:
    a: Any = 42
    b: Any = "hello"
    c: Any = 3.14
    d: Any = None
    e: Any = True
    print("constructed")


main()
