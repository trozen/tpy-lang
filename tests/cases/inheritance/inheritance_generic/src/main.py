from tpy import int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value


class IntContainer(Container[int32]):
    extra: int32

    def __init__(self, value: int32, extra: int32) -> None:
        self.value = value
        self.extra = extra


c = IntContainer(42, 100)
print(c.value)   # 42
print(c.get())   # 42
print(c.extra)   # 100
