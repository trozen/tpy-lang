# list[Any]: each element coerces into Any at the list-literal element
# position (a coercion context).

from typing import Any


def main() -> None:
    items: list[Any] = [1, "hello", 3.14, None, True]
    print(len(items))


main()
