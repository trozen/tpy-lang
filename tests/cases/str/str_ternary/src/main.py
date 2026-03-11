# Test ternary expression with str operands (PendingStrType comparison fix).
def main() -> None:
    a: str = "hello"
    b: str = "world"

    x = a if True else b
    print(x)

    y = a if False else b
    print(y)

    z = "literal_a" if True else "literal_b"
    print(z)

main()
