# A generic record's `T`-typed field reads, `T` returns and `T` parameter echo at
# a scalar instantiation -- the open-type-param value arms. It prints values.
from tpy import Int32, Own


class Cell[T]:
    value: T
    other: T

    def __init__(self, v: Own[T], o: Own[T]) -> None:
        self.value = v
        self.other = o

    def get(self) -> T:
        return self.value

    def pick(self) -> T:
        return self.other

    def echo(self, v: T) -> T:
        return v

    def store(self, v: T) -> None:
        self.value = v


def main() -> None:
    c = Cell[Int32](1, 2)
    print(c.get(), c.pick(), c.echo(7))
    c.store(9)
    print(c.get())


main()
