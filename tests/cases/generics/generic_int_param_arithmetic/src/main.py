from tpy import int32


class Container[T, N: int]:
    def get_double(self) -> int32:
        return int32(N * 2)

    def get_plus_one(self) -> int32:
        return int32(N + 1)

    def get_minus_five(self) -> int32:
        return int32(N - 5)


def main() -> None:
    c: Container[str, 10] = Container[str, 10]()
    print(c.get_double())      # 20
    print(c.get_plus_one())    # 11
    print(c.get_minus_five())  # 5


main()
