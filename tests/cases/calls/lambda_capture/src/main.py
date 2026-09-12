# Test lambda capturing a variable from enclosing scope
from tpy import int32, Fn


def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)


def main() -> None:
    offset: int32 = 100
    print(apply(lambda x: x + offset, 5))

    factor: int32 = 3
    print(apply(lambda x: x * factor, 7))


main()
