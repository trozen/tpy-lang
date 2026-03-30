# Multi-iterable map over rvalue iterables (owning iterator prevents dangling)
from tpy import Int32, Own

def make_xs() -> Own[list[Int32]]:
    return [1, 2, 3]

def make_ys() -> Own[list[Int32]]:
    return [10, 20, 30]

def add(a: Int32, b: Int32) -> Int32:
    return a + b

def main() -> None:
    # Both rvalue
    result = list(map(add, make_xs(), make_ys()))
    print(result)

    # Mixed: lvalue + rvalue
    xs = [1, 2, 3]
    result2 = list(map(add, xs, make_ys()))
    print(result2)

    # Rvalue with lambda
    result3 = list(map(lambda a, b: a * b, make_xs(), make_ys()))
    print(result3)

main()
