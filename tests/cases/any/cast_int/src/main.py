# typing.cast(int, any_var) extracts the contained value when the
# typeid matches. Result is statically typed as int (BigInt).

from typing import Any, cast


def main() -> None:
    x: Any = 42
    n = cast(int, x)
    print(n + 1)


main()
