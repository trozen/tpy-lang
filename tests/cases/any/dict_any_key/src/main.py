# dict[Any, V] is allowed: keys hash via the Any cell's hash slot.

from typing import Any


def main() -> None:
    d: dict[Any, int] = {}
    k: Any = "name"
    d[k] = 42
    print(len(d))


main()
