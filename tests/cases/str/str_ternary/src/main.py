# Test ternary expression with str operands (PendingStrType comparison fix).
def main() -> None:
    a: str = "hello"
    b: str = "world"

    x = a if True else b  # tpyc: type(StrView)
    print(x)

    y = a if False else b  # tpyc: type(StrView)
    print(y)

    z = "literal_a" if True else "literal_b"  # tpyc: type(StrView)
    print(z)

main()
