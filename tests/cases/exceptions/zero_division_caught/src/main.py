# Runtime division by zero throws ZeroDivisionError -- catchable in user code.
# Exercises the float, fixed-int, BigInt, and divmod paths. We print a fixed
# per-path token, not str(e): CPython 3.14 collapsed the granular messages
# ("float division by zero", "integer modulo by zero", ...) all to
# "division by zero", so pinning the message would make the output
# CPython-version-specific. The token still proves each path throws a
# ZeroDivisionError caught by the clause.
#
# `int / 0` (the integer true-division path) is covered by the
# panic_int_truediv_zero case.

from tpy import Int32


def main() -> None:
    # Float true-division.
    try:
        a: float = 10.0
        b: float = 0.0
        print(a / b)
    except ZeroDivisionError:
        print("caught: float /")

    # Float floor division.
    try:
        c: float = 10.0
        d: float = 0.0
        print(c // d)
    except ZeroDivisionError:
        print("caught: float //")

    # Float modulo.
    try:
        e: float = 10.0
        f: float = 0.0
        print(e % f)
    except ZeroDivisionError:
        print("caught: float %")

    # Fixed-int floor division.
    try:
        g: Int32 = Int32(10)
        h: Int32 = Int32(0)
        print(g // h)
    except ZeroDivisionError:
        print("caught: int //")

    # Fixed-int modulo.
    try:
        i: Int32 = Int32(10)
        j: Int32 = Int32(0)
        print(i % j)
    except ZeroDivisionError:
        print("caught: int %")

    # BigInt floor division.
    try:
        k: int = 10
        m: int = 0
        print(k // m)
    except ZeroDivisionError:
        print("caught: bigint //")

    # BigInt modulo.
    try:
        n: int = 10
        o: int = 0
        print(n % o)
    except ZeroDivisionError:
        print("caught: bigint %")

    # divmod on fixed-int.
    try:
        p: Int32 = Int32(10)
        q: Int32 = Int32(0)
        print(divmod(p, q))
    except ZeroDivisionError:
        print("caught: int divmod")

    # divmod on float.
    try:
        r: float = 10.0
        s: float = 0.0
        print(divmod(r, s))
    except ZeroDivisionError:
        print("caught: float divmod")


main()
