# The OPERATOR spelling of a user comparison dunder whose operand is not a
# record: `t == s` off `__eq__(self, other: str)`, the `!=` C++ rewrites from
# it, an ordering dunder, and the reflected spelling `s == t` -- plus the other
# operand families the row admits (int32, bytes, char). Every leg is compared
# against CPython, so a silently narrowing operand would show up as a
# divergent line rather than as a render difference.
from tpy import char, float64, int32


class Tag:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: str) -> bool:
        return self.name == other

    def __lt__(self, other: str) -> bool:
        return self.name < other


class Count:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: int32) -> bool:
        return self.n == other


class Blob:
    data: bytes

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __eq__(self, other: bytes) -> bool:
        return self.data == other


class Ratio:
    r: float64

    def __init__(self, r: float64) -> None:
        self.r = r

    def __eq__(self, other: float64) -> bool:
        return self.r == other


class Initial:
    c: char

    def __init__(self, c: char) -> None:
        self.c = c

    def __eq__(self, other: char) -> bool:
        return self.c == other


def main() -> None:
    t = Tag("b")
    s = "b"
    o = "c"
    print(t == s)  # tpyc: ok
    print(t == o)  # tpyc: ok
    # Derived from __eq__ -- no dunder of its own on either side.
    print(t != o)  # tpyc: ok
    print(t < o)  # tpyc: ok
    # The reflected spelling: CPython retries through Tag.__eq__.
    print(s == t)  # tpyc: ok
    # The explicit call, which already routed -- the two spellings must agree.
    print(t.__eq__(s))  # tpyc: ok

    # The remaining operand families, each against a literal its own dunder
    # slot accepts. The int32 legs are the ones a narrowing conversion would
    # have made diverge from CPython.
    c = Count(2)
    print(c == 2)  # tpyc: ok
    print(c != 3)  # tpyc: ok
    b = Blob(b"xy")
    print(b == b"xy")  # tpyc: ok
    print(b != b"ab")  # tpyc: ok
    # The reflected bytes spelling puts the literal LEFT; CPython retries
    # through Blob.__eq__ exactly as C++20's reversed candidate does.
    print(b"xy" == b)  # tpyc: ok
    # An int literal at a `float` slot is a WIDENING the gate must not refuse
    # (unlike the narrowing one the error case pins).
    r = Ratio(2.0)
    print(r == 2)  # tpyc: ok
    print(r != 3)  # tpyc: ok
    i = Initial(char("a"))
    print(i == char("a"))  # tpyc: ok
    print(i != char("b"))  # tpyc: ok


main()
