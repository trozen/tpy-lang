# An exception subclass whose base initializer takes a COMPUTED message: the
# str concat and the str() conversion are pure expressions, so they render
# inside the member-init list, which has no place to hoist a temp.
from tpy import Int32


class Tagged(Exception):
    n: Int32

    def __init__(self, n: Int32) -> None:
        super().__init__("tag" + str(n))  # concat + conversion in the base cell
        self.n = n


class Labelled(Exception):
    def __init__(self, prefix: str, t: Tagged) -> None:
        # A field read of a record param is a scalar operand like any other.
        super().__init__(prefix + "-" + str(t.n))


class Numbered(Exception):
    n: Int32

    def __init__(self, n: Int32) -> None:
        # The conversion standing alone, with no concat around it.
        super().__init__(str(n))
        self.n = n


class Formatted(Exception):
    n: Int32

    def __init__(self, n: Int32) -> None:
        # std::format is a pure expression too, so the f-string spelling of
        # the same message lands in the member-init list as well.
        super().__init__(f"tag{n}")
        self.n = n


def main() -> None:
    try:
        raise Tagged(5)
    except Tagged as e:
        print(e.n, str(e))
    try:
        raise Labelled("lab", Tagged(9))
    except Labelled as e2:
        print(str(e2))
    try:
        raise Numbered(7)
    except Numbered as e3:
        print(e3.n, str(e3))
    try:
        raise Formatted(8)
    except Formatted as e4:
        print(e4.n, str(e4))


main()
