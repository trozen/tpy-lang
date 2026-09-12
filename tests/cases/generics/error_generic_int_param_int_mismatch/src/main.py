from tpy import int32


class Container[T, N: int]:
    value: T


def main() -> None:
    # ERROR: T requires a type, got integer 10
    c: Container[10, 5] = Container[10, 5]()  # tpyc: error(/requires a type/)


main()
