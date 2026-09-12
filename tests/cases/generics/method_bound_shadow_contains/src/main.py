# Happy path: `__contains__[T: Equatable]` on `class MyBag[T]` with a
# satisfying T. Mirrors the error_method_bound_shadow_contains case but
# exercises the non-violation branch of the dispatch-site bound check.
from tpy import int32, Equatable


class MyBag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, value: T) -> None:
        self.items.append(value)

    def __contains__[T: Equatable](self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


def main() -> None:
    b: MyBag[int32] = MyBag()
    b.add(1)
    b.add(2)
    b.add(3)
    print(int32(2) in b)
    print(int32(5) in b)
    print(int32(5) not in b)


main()
