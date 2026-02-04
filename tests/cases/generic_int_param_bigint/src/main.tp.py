from tpy import Int32


class Container[T, N: int]:
    def get_size_as_bigint(self) -> int:
        return N  # N coerces to BigInt


def main() -> None:
    c: Container[str, 42] = Container[str, 42]()
    size: int = c.get_size_as_bigint()
    print(size)


main()
