# Auto-coerce panics on typeid mismatch -- same runtime check as
# typing.cast. CPython would silently assign (no check), so this skips
# the cpy phase by panic-test convention.

from typing import Any


def main() -> None:
    a: Any = "not an int"
    n: int = a
    print(n)


main()
