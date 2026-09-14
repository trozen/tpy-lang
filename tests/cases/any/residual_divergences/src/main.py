# Acknowledged divergences from CPython inside Any, per
# docs/ANY_TYPE_DESIGN.md ("Residual divergences"): a `char` is a
# str-of-one CPython has no spelling for, so it never equals a `str`; and a
# record with no `__eq__` compares by identity in CPython, which an Any that
# COPIED the object cannot answer, so two cells holding one object compare
# False. Plus the `char` payload itself: its own typeid, cast back, narrowed
# and compared.

from typing import Any, cast
from tpy import char, int32


class Rec:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    # char vs str: CPython ('a' == 'a') would print True
    c: Any = char("a")
    s: Any = "a"
    print("char-vs-str", c == s, s == c)

    # one object stored twice: CPython's identity fallback would print True
    r = Rec(1)
    a: Any = r  # tpyc: warning(/copies Rec into Any/)
    b: Any = r  # tpyc: warning(/copies Rec into Any/)
    print("same-object", a == b)
    print("still-owned", r.n)

    # char payload: stored under its own typeid, so it casts, narrows and
    # compares as a char
    c2: Any = char("a")
    d: Any = char("b")
    print("char", c == c2, c == d, cast(char, c) == char("a"))  # tpyc: ok
    if isinstance(c, char):
        print("narrowed", c)


main()
