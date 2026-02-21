from tpy import Int32

class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value


class Container[T]:
    inner: Box[T]

    def __init__(self, value: T) -> None:
        self.inner = Box[T](value)

    def get_inner(self) -> Box[T]:
        return self.inner

    def get_value(self) -> T:
        return self.inner.get()


def main() -> None:
    # Test Container[Int32] which internally uses Box[Int32]
    c: Container[Int32] = Container[Int32](42)
    print(c.get_value())

    # Get the inner box
    box: Box[Int32] = c.get_inner()
    print(box.get())

    # Local variable with type parameter (tests the second fix)
    c2: Container[str] = Container[str]("hello")
    print(c2.get_value())


main()
