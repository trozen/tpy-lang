# Base-init args rendered as written: a reference-type name at a `const T&`
# parameter (bytearray included), a str literal or conversion at a `StrView`.
from tpy import int32, StrView


class Base:
    buf: bytearray
    xs: list[int32]

    def __init__(self, b: bytearray, xs: list[int32]) -> None:
        self.buf = b  # tpyc: warning(/copies bytearray into field/)
        self.xs = xs  # tpyc: warning(/copies list\[int32\] into field/)


class Child(Base):
    n: int32

    def __init__(self, b: bytearray, xs: list[int32]) -> None:
        super().__init__(b, xs)  # tpyc: ok
        self.n = 1


class Label:
    s: str

    def __init__(self, s: StrView) -> None:
        self.s = str(s)


class Numbered(Label):
    def __init__(self, n: int32) -> None:
        # a conversion at a `StrView` base parameter
        super().__init__(str(n))  # tpyc: ok


class Fixed(Label):
    def __init__(self) -> None:
        # a str literal at a `StrView` base parameter
        super().__init__("abc")  # tpyc: ok


def main() -> None:
    # The field write itself is a warned copy (both legs), so the case pins
    # the warning rather than aliasing; the subject is the base-init ARG.
    c = Child(bytearray(b"xy"), [1, 2, 3])
    print(len(c.buf), len(c.xs), c.n)
    print("str:", Numbered(42).s, Fixed().s)


main()
