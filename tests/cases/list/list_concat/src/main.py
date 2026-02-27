# Test list concatenation with + and += operators,
# including inferred types and literal operands.
def test_add() -> None:
    a: list[int] = [1, 2]
    b: list[int] = [3, 4]
    c: list[int] = a + b
    print(c)

def test_iadd() -> None:
    a: list[int] = [10, 20]
    b: list[int] = [30, 40]
    a += b
    print(a)

def test_empty() -> None:
    a: list[int] = []
    b: list[int] = [1, 2, 3]
    print(a + b)
    print(b + a)

def test_inferred() -> None:
    a = [1, 2, 3]
    b = [4, 5]
    print(a + b)

def test_literal() -> None:
    print([10, 20] + [30])

def test_mixed_annotated_literal() -> None:
    a: list[int] = [1, 2]
    print(a + [3, 4])

def test_strings() -> None:
    s1 = ["a", "b"]
    s2 = ["c"]
    print(s1 + s2)

def main() -> None:
    test_add()
    test_iadd()
    test_empty()
    test_inferred()
    test_literal()
    test_mixed_annotated_literal()
    test_strings()

main()
