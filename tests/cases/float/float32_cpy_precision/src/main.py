# float32 arithmetic is single precision in both TPy and, through the lib/cpy
# stub, CPython. Operands are float32, int, or float LITERALS; a float-typed
# variable operand is the declared stub limit (BUGS.md#cpy-float32-double-precision).
from tpy import float32


# free function: construction and binary operators
def ops() -> None:
    a = float32(0.1)
    b = float32(0.2)
    print("ctor", a, b)
    print("add", a + b)
    print("sub", b - a)
    print("mul", a * 3)
    print("div", b / 3)
    print("lit", a + 0.5)


# free function: the reflected forms, floor division, modulo and power
def reflected() -> None:
    a = float32(0.1)
    print("radd", 1 + a)
    print("rmul", 3 * a)
    print("floordiv", float32(7.5) // 2)
    print("mod", float32(7.5) % 2)
    print("pow", float32(1.1) ** 2)
    print("powf", float32(1.1) ** float32(2.0))


# free function: unary operators
def unary() -> None:
    a = float32(0.1)
    print("neg", -a)
    print("abs", abs(-a))


# free function: accumulation drifts the single-precision way
def accumulate() -> None:
    acc = float32(0.0)
    for _ in range(10):
        acc = acc + float32(0.1)
    print("acc", acc)


# method: a field of the type keeps the rounding through a method
class Meter:
    total: float32

    def __init__(self) -> None:
        self.total = float32(0.0)

    def add(self, v: float32) -> None:
        self.total = self.total + v


def method() -> None:
    m = Meter()
    m.add(float32(0.1))
    m.add(float32(0.7))
    print("method", m.total)


# comprehension: element-wise rounding
def comprehension() -> None:
    xs = [float32(0.1) * k for k in range(1, 4)]
    print("comp", xs)


# union slot: a float literal is a double, so it lands in float
def union_literal() -> None:
    u: float32 | float = 0.5
    match u:
        case float32():
            print("union-literal float32")
        case float():
            print("union-literal float")


# union slot: a float32 value lands in float32 at a parameter; the local form
# prints only, since sema narrows a declared union local to the assigned type
def union_value(v: float32) -> None:
    u: float32 | float = v
    print("union-value", u)


def union_param(u: float32 | float) -> None:
    match u:
        case float32():
            print("union-param float32")
        case float():
            print("union-param float")


def main() -> None:
    ops()
    reflected()
    unary()
    accumulate()
    method()
    comprehension()
    union_literal()
    union_value(float32(1.5))
    union_param(float32(2.5))


main()
