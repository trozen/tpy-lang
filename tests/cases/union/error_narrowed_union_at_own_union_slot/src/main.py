# A ptr-variant union name at an `Own[A | B]` slot: outside a narrow the arm
# copies the active member out, but INSIDE one the concrete alternative is what
# is bound, which the variant lift has no arm for, so `consume(p)` is rejected.
from tpy import Int32, Own


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def consume(v: Own[A | B]) -> None:
    pass


def narrowed_inside(p: A | B) -> None:
    if isinstance(p, A):
        consume(p)  # tpyc: error(/call\.arg_shape/)


def main() -> None:
    narrowed_inside(A(2))


main()
