from tpy import int32


class Base[T, N: int]:
    value: T

    def __init__(self, v: T) -> None:
        self.value = v


class Child[T, N: int](Base[T, N]):
    extra: int32

    def __init__(self, v: T, e: int32) -> None:
        super().__init__(v)
        self.extra = e


class GrandChild[T, N: int](Child[T, N]):
    name: str

    def __init__(self, v: T, e: int32, n: str) -> None:
        super().__init__(v, e)
        self.name = n


def main() -> None:
    # Test forwarding int param through inheritance chain
    b: Base[str, 10] = Base[str, 10]("base")
    print(b.value)

    c: Child[str, 20] = Child[str, 20]("child", int32(42))
    print(c.value)
    print(c.extra)

    g: GrandChild[str, 30] = GrandChild[str, 30]("grand", int32(100), "test")
    print(g.value)
    print(g.extra)
    print(g.name)


main()
