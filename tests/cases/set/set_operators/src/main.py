# Set operators: |, &, -, ^, <=, <, >=, >, |=, &=, -=, ^=
from tpy import Int32

def main() -> None:
    a: set[Int32] = {1, 2, 3}
    b: set[Int32] = {2, 3, 4}

    # Binary operators
    print(a | b)
    print(a & b)
    print(a - b)
    print(a ^ b)

    # Comparison operators (subset/superset)
    c: set[Int32] = {1, 2}
    print(c <= a)
    print(a <= a)
    print(c < a)
    print(a < a)
    print(a >= c)
    print(a > c)
    print(a > a)

    # Equality
    d: set[Int32] = {3, 2, 1}
    print(a == d)
    print(a != b)

    # In-place operators
    e: set[Int32] = {1, 2}
    e |= b
    print(e)
    f: set[Int32] = {1, 2, 3, 4}
    f &= a
    print(f)
    g: set[Int32] = {1, 2, 3}
    g -= b
    print(g)
    h: set[Int32] = {1, 2, 3}
    h ^= b
    print(h)

main()
