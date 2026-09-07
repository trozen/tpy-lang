# Unpacking a namespace-scope STORAGE tuple global: the global slot's element
# spelling needs the storage lift the unpack targets do not carry.
from tpy import Int32


class Elem:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


t1 = Elem(10)
t2 = Elem(20)
g: tuple[Elem | None, Elem | None] = (t1, t2)


def main() -> None:
    a, b = g  # tpyc: error(/stmt.tuple_unpack/)
    if a is not None:
        print(a.x)


main()
