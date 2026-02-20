# isinstance in compound conditions: and/or, negation, multi-variable
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

def test_and_rhs(v: A | B) -> int:
    if isinstance(v, A) and v.x > 0:
        return v.x
    return -1

def test_and_true(v: A | B) -> int:
    if isinstance(v, A) and True:
        return v.x
    return -1

def test_or_rhs(v: A | B) -> bool:
    return isinstance(v, A) or v.y > 0

def test_negation(v: A | B) -> int:
    if not isinstance(v, A):
        return v.y
    else:
        return v.x

def test_multi_var(a: A | B, b: A | B) -> int:
    if isinstance(a, A) and isinstance(b, B):
        return a.x + b.y
    return 0

def main() -> None:
    print(test_and_rhs(A(42)))
    print(test_and_rhs(A(-1)))
    print(test_and_rhs(B(99)))
    print(test_and_true(A(5)))
    print(test_and_true(B(5)))
    print(test_or_rhs(A(1)))
    print(test_or_rhs(B(5)))
    print(test_or_rhs(B(-1)))
    print(test_negation(A(1)))
    print(test_negation(B(2)))
    print(test_multi_var(A(10), B(20)))
    print(test_multi_var(A(10), A(5)))
    print(test_multi_var(B(1), B(2)))

main()
