# A char at an `Own[char]` slot: char is a value type spelled `char`, so an
# rvalue-shaped actual (a select of two Chars, a char ctor) binds the by-value
# slot bare, exactly as an `Own[scalar]` slot does.
from tpy import char, int32, Own


class Sink:
    n: int32
    last: char

    def __init__(self) -> None:
        self.n = 0
        self.last = char("?")

    def put(self, c: Own[char]) -> None:
        self.n += 1
        self.last = c


def feed(s: Sink, flag: bool, a: char, b: char) -> None:
    s.put(a if flag else b)    # tpyc: ok -- a select at the Own[char] slot
    s.put(char("z"))           # tpyc: ok -- a ctor rvalue


def main() -> None:
    s = Sink()
    feed(s, True, char("a"), char("b"))
    print(s.n, s.last)
    feed(s, False, char("a"), char("b"))
    print(s.n, s.last)


main()
