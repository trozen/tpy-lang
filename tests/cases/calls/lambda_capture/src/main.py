# Test lambda capturing a variable from enclosing scope
from tpy import Int32, Fn


def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)


def main() -> None:
    offset: Int32 = 100
    print(apply(lambda x: x + offset, 5))

    factor: Int32 = 3
    print(apply(lambda x: x * factor, 7))


main()
