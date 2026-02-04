from tpy import Int32


class Container[T, N: int]:
    value: T

    def __init__(self, v: T) -> None:
        self.value = v


def main() -> None:
    # Test basic integer type parameter
    c1: Container[str, 10] = Container[str, 10]("hello")
    print(c1.value)

    c2: Container[Int32, 5] = Container[Int32, 5](Int32(42))
    print(c2.value)

    c3: Container[str, 100] = Container[str, 100]("world")
    print(c3.value)


main()
