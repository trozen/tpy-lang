# Unpacking a tuple-of-Optional-references GLOBAL in a function body: the
# global is a tuple of pointer slots (`std::tuple<Elem*, Elem*>`), so the
# unpack binds it by reference and a narrowed target aliases the object the
# slot points at.
from tpy import int32


class Elem:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


t1 = Elem(10)
t2 = Elem(20)
g: tuple[Elem | None, Elem | None] = (t1, None)


def main() -> None:
    a, b = g  # tpyc: ok
    if a is not None:
        a.x = 11
    print(t1.x, b is None)


main()
