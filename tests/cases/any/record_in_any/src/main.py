# Storing a user record in Any: the ops slots that the record doesn't
# implement (no __bool__, possibly no __str__) fall back to safe defaults
# rather than failing at C++ template instantiation. Ops the record DOES
# implement route through normally.

from typing import Any


class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __str__(self) -> str:
        return f"Point({self.x}, {self.y})"


def main() -> None:
    p = Point(1, 2)
    a: Any = p
    print(a)              # uses __str__
    if a:                 # to_bool fallback -> True (Python default)
        print("truthy")
    if isinstance(a, Point):
        print(a.x + a.y)


main()
