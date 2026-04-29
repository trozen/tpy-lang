# Auto-coerce on annotated assignment: `n: int = any_var` extracts via
# any_cast_or_panic<BigInt> at the assignment site.

from typing import Any


def main() -> None:
    a: Any = 42
    n: int = a
    print(n + 1)


main()
