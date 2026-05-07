# Runtime division by zero throws ZeroDivisionError -- catchable in user code.
# Exercises the float, fixed-int, BigInt, and divmod paths; CPython-aligned
# messages distinguish operand kind ("float division by zero" vs
# "integer modulo by zero" vs "integer division or modulo by zero") and
# operation ("float modulo", "float floor division by zero", "float divmod()").
#
# `int / 0` (TPy converts int operands to float for true division) is
# covered by the panic_int_truediv_zero case; TPy emits "float division
# by zero" there, while CPython says "division by zero" -- a documented
# divergence (see EXCEPTION_DESIGN.md).

from tpy import Int32


def main() -> None:
    # Float true-division.
    try:
        a: float = 10.0
        b: float = 0.0
        print(a / b)
    except ZeroDivisionError as e:
        print("caught:", str(e))

    # Float floor division.
    try:
        c: float = 10.0
        d: float = 0.0
        print(c // d)
    except ZeroDivisionError as e:
        print("caught:", str(e))

    # Float modulo.
    try:
        e: float = 10.0
        f: float = 0.0
        print(e % f)
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # Fixed-int floor division.
    try:
        g: Int32 = Int32(10)
        h: Int32 = Int32(0)
        print(g // h)
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # Fixed-int modulo.
    try:
        i: Int32 = Int32(10)
        j: Int32 = Int32(0)
        print(i % j)
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # BigInt floor division.
    try:
        k: int = 10
        m: int = 0
        print(k // m)
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # BigInt modulo.
    try:
        n: int = 10
        o: int = 0
        print(n % o)
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # divmod on fixed-int.
    try:
        p: Int32 = Int32(10)
        q: Int32 = Int32(0)
        print(divmod(p, q))
    except ZeroDivisionError as ex:
        print("caught:", str(ex))

    # divmod on float.
    try:
        r: float = 10.0
        s: float = 0.0
        print(divmod(r, s))
    except ZeroDivisionError as ex:
        print("caught:", str(ex))


main()
