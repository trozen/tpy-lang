# Everyday truthiness: a condition is decided by the operand's TYPE, so the
# same scalar renders the same test whether it is read from a container, a
# field, a call, an arithmetic expression or a literal.
from tpy import Char, Int32


class Holder:
    n: Int32
    ratio: float
    tag: str
    rows: list[Int32]

    def __init__(self) -> None:
        self.n = 2
        self.ratio = 0.5
        self.tag = "t"
        self.rows = [7]

    def size(self) -> Int32:
        return self.n


def width() -> Int32:
    return 3


def zero() -> Int32:
    return 0


def scalar_shapes(h: Holder, xs: list[Int32], c: Char) -> Int32:
    seen = 0
    if xs[0]:  # scalar ELEMENT read
        seen += 1
    if xs[1]:
        seen += 2
    if h.n:  # scalar FIELD read
        seen += 4
    if h.ratio:  # float field
        seen += 8
    if h.size():  # int-returning METHOD call
        seen += 16
    if width():  # int-returning FREE call
        seen += 32
    if zero():
        seen += 64
    if seen + 1:  # ARITHMETIC operand
        seen += 128
    if 1:  # numeric LITERAL
        seen += 256
    if c:  # Char local
        seen += 512
    return seen


def moded_shapes(h: Holder, names: list[str],
                 rows: list[list[Int32]]) -> Int32:
    seen = 0
    if h.tag:  # str field: the empty-test wraps a field read too
        seen += 16
    if names[0]:  # str element: the empty-test wraps the same read
        seen += 1
    if names[1]:
        seen += 2
    if rows[0]:  # list element: the __len__ wrap over the same read
        seen += 4
    if rows[1]:
        seen += 8
    return seen


def operand_positions(xs: list[Int32], n: Int32) -> Int32:
    seen = 0
    if n and xs[0]:  # a scalar condition composes as a logical OPERAND
        seen += 1
    if width() or xs[1]:
        seen += 2
    if not xs[1]:  # ... and under `not`
        seen += 4
    seen += 8 if xs[0] else 0  # ... and as a ternary CONDITION
    if n if xs[0] else 0:  # ... and as the ternary's own truthiness
        seen += 16
    return seen


def guard_shapes(h: Holder, xs: list[Int32], names: list[str]) -> Int32:
    # A match guard is a boolean context too, so the same shapes hold there.
    match h.n:
        case 1 if xs[0]:
            return 1
        case 2 if h.size():
            return 2
        case 3 if h.n + 1:
            return 3
        case 4 if names[0]:
            return 4
        case _ if h.tag:
            return 5
        case _:
            return 0
    return 0


def drain(xs: list[Int32]) -> Int32:
    popped = 0
    while len(xs):  # int-returning call in a loop head
        xs.pop()
        popped += 1
    return popped


def mutate_through_field(h: Holder) -> None:
    # The field read the condition consumes is the LIVE container, not a
    # copy: append through the field and the same condition sees it.
    while len(h.rows) < 3:
        h.rows.append(1)
    print("rows", len(h.rows))


def main() -> None:
    h = Holder()
    xs = [1, 0]
    names = ["", "a"]
    rows: list[list[Int32]] = []
    rows.append([1])
    blank: list[Int32] = []
    rows.append(blank)
    print("scalar", scalar_shapes(h, xs, Char("x")))
    print("moded", moded_shapes(h, names, rows))
    print("operands", operand_positions(xs, 1))
    print("guards", guard_shapes(h, xs, names))
    print("drained", drain([1, 2, 3]))
    mutate_through_field(h)


main()
