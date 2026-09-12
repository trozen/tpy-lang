# A class-shadowed bounded method param (`def m[T: Bound]` on `class C[T, N]`)
# must resolve through SUBCLASS receivers: the declaring base's params reach
# the dispatch site only via the composed class subst, so classification must
# use it (regression: such calls failed "Cannot infer type arguments").
# Covers a plain subclass, a grandchild (MRO-composed subst), and a generic
# subclass (subst composition through the child's own param).
from tpy import int32, Equatable, Comparable


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

    def biggest[T: Comparable](self) -> T:
        # Return a field element (not a local): a bare-T local return would
        # need Own[T] under the borrow rules; irrelevant to what this pins.
        idx: int32 = 0
        for i in range(1, len(self.items)):
            if self.items[i] > self.items[idx]:
                idx = i
        return self.items[idx]


class IntBag(Bag[int32, 4]):
    pass


class DeepBag(IntBag):
    pass


class WideBag[U](Bag[U, 8]):
    pass


def main() -> None:
    b = IntBag()
    b.add(3)
    b.add(7)
    print(b.contains(7))
    print(b.contains(9))
    print(b.biggest())

    d = DeepBag()
    d.add(5)
    d.add(2)
    print(d.contains(2))
    print(d.biggest())

    w = WideBag[int32]()
    w.add(11)
    w.add(4)
    print(w.contains(11))
    print(w.biggest())


main()
