# An int/int quotient beyond double range panics with OverflowError (CPython parity).
def main() -> None:
    a: int = 10**400
    b: int = 3
    print(a / b)


main()
