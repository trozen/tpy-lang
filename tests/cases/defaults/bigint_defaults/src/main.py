# Python int (BigInt) with default parameter values
def add(a: int, b: int = 0) -> int:
    return a + b

def scale(value: int, factor: int = 1) -> int:
    return value * factor

def main() -> None:
    print(add(5))
    print(add(5, 3))

    print(scale(10))
    print(scale(10, 4))

main()
