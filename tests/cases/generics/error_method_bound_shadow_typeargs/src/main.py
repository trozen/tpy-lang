# Explicit type args on a fully-shadowed generic method (every method type
# param is bound by the class instantiation) must be rejected loudly, not
# silently discarded -- through a subclass receiver the args would otherwise
# be name-captured by the composed class subst and ignored.
from tpy import Int32, StrView, Equatable


class Bag[T, N: int]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, value: T) -> None:
        self.items.append(value)

    def contains[T: Equatable](self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


class IntBag(Bag[Int32, 4]):
    pass


def main() -> None:
    b = IntBag()
    b.add(1)
    if b.contains[StrView](1):  # tpyc: error(/expects 0 type argument\(s\), got 1/)
        print("found")


main()
