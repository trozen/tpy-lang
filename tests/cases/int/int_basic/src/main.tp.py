"""Test basic int (BigInt) operations."""


def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def test_arithmetic():
    a = 10
    b = 3

    # Basic arithmetic
    print(a + b)  # 13
    print(a - b)  # 7
    print(a * b)  # 30
    print(a // b)  # 3 (floor division)
    print(a % b)  # 1

    # Negative division (Python semantics)
    c = -7
    d = 3
    print(c // d)  # -3 (not -2!)
    print(c % d)   # 2 (not -1!)


def test_comparison():
    x = 42
    y = 100

    if x < y:
        print("x < y")

    if x != y:
        print("x != y")

    if x == 42:
        print("x == 42")


# Top-level execution
print(factorial(10))  # 3628800
print(factorial(20))  # 2432902008176640000 (fits in 63 bits)

test_arithmetic()
test_comparison()

# Unary negation
neg = -42
print(neg)
