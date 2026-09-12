from tpy import int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get_value(self) -> T:
        return self.value


class Child[T](Container[list[T]]):
    extra: int32

    def __init__(self, value: list[T], extra: int32) -> None:
        self.value = value
        self.extra = extra

    def get_extra(self) -> int32:
        return self.extra


items: list[str] = ["hello", "world"]
c: Child[str] = Child[str](items, int32(42))
val: list[str] = c.get_value()
print(val[0])
print(val[1])
print(c.get_extra())
