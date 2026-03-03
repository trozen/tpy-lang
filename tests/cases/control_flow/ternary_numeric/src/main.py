# Ternary expression: numeric type widening between branches
from tpy import Int32, Int64

def wider(flag: bool, a: Int32, b: Int64) -> Int64:
    # Int32 + Int64 -> Int64
    return a if flag else b

def literal_with_typed(flag: bool, x: Int32) -> Int32:
    # Int literal adopts the concrete type from the other branch
    return x if flag else 0

def both_literals(flag: bool) -> None:
    # Both branches are int literals -> default int type
    x = 10 if flag else 20
    print(x)

def main() -> None:
    print(wider(True, 42, 100))
    print(wider(False, 42, 100))

    print(literal_with_typed(True, 5))
    print(literal_with_typed(False, 5))

    both_literals(True)
    both_literals(False)

main()
