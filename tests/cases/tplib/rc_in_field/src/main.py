# Rc[T] as a field of a record. Mutation via clone is visible through
# the record's clone too.
from tpy import Int32, Own
from tplib import Rc, make_rc


class Counter:
    value: Int32

    def __init__(self) -> None:
        self.value = Int32(0)

    def bump(self) -> None:
        self.value += Int32(1)


class Holder:
    shared: Rc[Counter]
    name: str

    def __init__(self, name: str, shared: Own[Rc[Counter]]) -> None:
        self.name = name
        self.shared = shared


def main() -> None:
    counter = make_rc(Counter())
    h1 = Holder("first", counter.clone())
    h2 = Holder("second", counter.clone())

    h1.shared.get().bump()
    h2.shared.get().bump()
    counter.get().bump()

    print(h1.name, h1.shared.get().value)
    print(h2.name, h2.shared.get().value)
    print("orig", counter.get().value)


main()
