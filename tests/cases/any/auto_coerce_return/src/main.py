# Auto-coerce at the return statement: `return any_var` where the
# function's declared return type is concrete extracts via any_cast.

from typing import Any


def extract(a: Any) -> int:
    return a


def main() -> None:
    a: Any = 99
    print(extract(a))


main()
