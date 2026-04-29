# Inline isinstance narrowing on Any inside &&/|| conditions: the RHS
# of `and` / `or` sees the variable narrowed to T. The narrowed binding
# is a `std::any_cast<const T&>` borrow, not a `std::get<T>` (which
# would be variant-flavoured and miscompile against tpy::Any).

from typing import Any


def main() -> None:
    a: Any = 5
    if isinstance(a, int) and a > 0:
        print("positive int")
    if isinstance(a, str) or len("x") > 0:
        print("either str or non-empty literal")


main()
