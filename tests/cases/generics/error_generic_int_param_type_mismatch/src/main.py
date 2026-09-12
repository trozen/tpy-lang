from tpy import int32


class Container[T, N: int]:
    value: T


def main() -> None:
    # ERROR: N requires an integer, got str
    c: Container[str, str] = Container[str, str]()  # tpyc: error(/requires an integer/)


main()
