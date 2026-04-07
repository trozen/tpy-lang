# User-defined operators on Ref[T]: field access and parameter references
# should auto-deref for dunder dispatch
from __future__ import annotations
from tpy import Own

class Vec:
    x: float
    y: float

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Vec) -> Own[Vec]:
        return Vec(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec) -> Own[Vec]:
        return Vec(self.x - other.x, self.y - other.y)

    def __mul__(self, s: float) -> Own[Vec]:
        return Vec(self.x * s, self.y * s)

    def __neg__(self) -> Own[Vec]:
        return Vec(-self.x, -self.y)

    def __eq__(self, other: Vec) -> bool:
        return self.x == other.x and self.y == other.y

    def __iadd__(self, other: Vec) -> Vec:
        self.x += other.x
        self.y += other.y
        return self


class Segment:
    start: Vec
    end: Vec

    def __init__(self, start: Vec, end: Vec) -> None:
        self.start = start
        self.end = end

    def diff(self) -> Own[Vec]:
        # Ref[Vec] - Ref[Vec] via field access
        return self.end - self.start

    def midpoint(self) -> Own[Vec]:
        # (Ref[Vec] + Ref[Vec]) * float
        return (self.start + self.end) * 0.5


def add_params(a: Vec, b: Vec) -> Own[Vec]:
    # Ref[Vec] + Ref[Vec] via parameter references
    return a + b


def scale_param(v: Vec, s: float) -> Own[Vec]:
    # Ref[Vec] * float via parameter reference
    return v * s


def negate_param(v: Vec) -> Own[Vec]:
    # -Ref[Vec] via parameter reference
    return -v


def compare_params(a: Vec, b: Vec) -> bool:
    # Ref[Vec] == Ref[Vec]
    return a == b


def test_field_ops() -> None:
    seg: Segment = Segment(Vec(1.0, 2.0), Vec(4.0, 6.0))
    d: Vec = seg.diff()
    print(d.x, d.y)
    m: Vec = seg.midpoint()
    print(m.x, m.y)


def test_param_ops() -> None:
    a: Vec = Vec(1.0, 2.0)
    b: Vec = Vec(3.0, 4.0)
    s: Vec = add_params(a, b)
    print(s.x, s.y)
    sc: Vec = scale_param(a, 3.0)
    print(sc.x, sc.y)
    n: Vec = negate_param(b)
    print(n.x, n.y)
    print(compare_params(a, a))
    print(compare_params(a, b))


def test_chained() -> None:
    a: Vec = Vec(1.0, 0.0)
    b: Vec = Vec(0.0, 1.0)
    # Own[Vec] + Ref[Vec]
    c: Vec = a * 2.0 + b
    print(c.x, c.y)
    # Ref[Vec] + Own[Vec]
    d: Vec = a + b * 3.0
    print(d.x, d.y)


def test_binop_assign() -> None:
    v: Vec = Vec(1.0, 2.0)
    inc: Vec = Vec(10.0, 20.0)
    # binary op with Ref[Vec] on rhs, result assigned back
    v = v + inc
    print(v.x, v.y)


def test_iadd_ref(delta: Vec) -> None:
    # __iadd__ with Ref[Vec] rhs (param reference)
    v: Vec = Vec(0.0, 0.0)
    v += delta
    print(v.x, v.y)


def main() -> None:
    test_field_ops()
    test_param_ops()
    test_chained()
    test_binop_assign()
    test_iadd_ref(Vec(5.0, 7.0))

main()
