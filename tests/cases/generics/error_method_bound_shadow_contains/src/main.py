# Regression: __contains__ dispatch (`in` / `not in`) on a generic container
# whose __contains__ has `[T: Equatable]` shadowing class T. The class-shadowed
# bound must be enforced at the dispatch site, not silently passed to C++.
from tpy import Equatable

class NotEquatable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class MyBag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def __contains__[T: Equatable](self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


def main() -> None:
    b = MyBag[NotEquatable]()
    n = NotEquatable(1)
    if n in b:  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)
        print("found")


main()
