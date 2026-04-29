# isinstance narrowing on Any: inside the true branch, the variable is
# bound to a `const T&` borrow into the cell's contents.

from typing import Any


def main() -> None:
    x: Any = 42
    if isinstance(x, int):
        print(x + 1)
    else:
        print("not int")


main()
