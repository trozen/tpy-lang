# typing.cast(T, x) panics at runtime when the contained typeid does
# not match T. CPython would silently pass through; TPy adds the safety
# check (panic-test cases skip the cpy phase by convention).

from typing import Any, cast


def main() -> None:
    x: Any = "not an int"
    n = cast(int, x)
    print(n)


main()
