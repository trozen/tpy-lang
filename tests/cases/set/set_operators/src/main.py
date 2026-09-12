# Set operators: |, &, -, ^, <=, <, >=, >, |=, &=, -=, ^=
from tpy import int32

def main() -> None:
    a: set[int32] = {1, 2, 3}
    b: set[int32] = {2, 3, 4}

    # Binary operators
    print(a | b)
    print(a & b)
    print(a - b)
    print(a ^ b)

    # Comparison operators (subset/superset)
    c: set[int32] = {1, 2}
    print(c <= a)
    print(a <= a)
    print(c < a)
    print(a < a)
    print(a >= c)
    print(a > c)
    print(a > a)

    # Equality
    d: set[int32] = {3, 2, 1}
    print(a == d)
    print(a != b)

    # In-place operators
    e: set[int32] = {1, 2}
    e |= b
    print(e)
    f: set[int32] = {1, 2, 3, 4}
    f &= a
    print(f)
    g: set[int32] = {1, 2, 3}
    g -= b
    print(g)
    h: set[int32] = {1, 2, 3}
    h ^= b
    print(h)

main()
