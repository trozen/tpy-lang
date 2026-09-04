# A Char at an `Own[Char]` slot: Char is a value type spelled `char`, so an
# rvalue-shaped actual (a select of two Chars, a Char ctor) binds the by-value
# slot bare, exactly as an `Own[scalar]` slot does.
from tpy import Char, Int32, Own


class Sink:
    n: Int32
    last: Char

    def __init__(self) -> None:
        self.n = 0
        self.last = Char("?")

    def put(self, c: Own[Char]) -> None:
        self.n += 1
        self.last = c


def feed(s: Sink, flag: bool, a: Char, b: Char) -> None:
    s.put(a if flag else b)    # tpyc: ok -- a select at the Own[Char] slot
    s.put(Char("z"))           # tpyc: ok -- a ctor rvalue


def main() -> None:
    s = Sink()
    feed(s, True, Char("a"), Char("b"))
    print(s.n, s.last)
    feed(s, False, Char("a"), Char("b"))
    print(s.n, s.last)


main()
