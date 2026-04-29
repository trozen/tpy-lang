# typing.cast(T, x) is a static-only no-op when x is not Any -- matches
# CPython's typing.cast semantics. Compiles cleanly with no runtime check.

from typing import cast


def main() -> None:
    x: int = 42
    n = cast(int, x)
    print(n)


main()
