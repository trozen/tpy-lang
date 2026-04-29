# Printing a container of Any values uses each cell's repr slot
# (matching CPython: dict / list / set repr quotes string values).
# The Any-element overload of `tpy::detail::print_element` routes
# through repr instead of operator<<'s str slot.

from typing import Any


def main() -> None:
    d: dict[str, Any] = {"name": "tpy", "version": 1, "debug": True}
    lst: list[Any] = [1, "hi", None, 2.5]
    print(d)
    print(lst)


main()
