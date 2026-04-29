# Auto-coerce at the function arg position: f(any_var) where f expects
# a concrete type extracts via any_cast_or_panic.

from typing import Any


def add_one(n: int) -> int:
    return n + 1


def main() -> None:
    a: Any = 5
    print(add_one(a))


main()
