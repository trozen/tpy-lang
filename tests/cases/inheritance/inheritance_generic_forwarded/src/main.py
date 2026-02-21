from tpy import Int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get_value(self) -> T:
        return self.value


class Wrapper[T](Container[T]):
    extra: Int32

    def __init__(self, value: T, extra: Int32) -> None:
        self.value = value
        self.extra = extra

    def get_extra(self) -> Int32:
        return self.extra


w: Wrapper[str] = Wrapper[str]("hello", Int32(42))
print(w.get_value())
print(w.get_extra())
