from tpy import Int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value


class LabeledContainer(Container[Int32]):
    label: str

    def __init__(self, label: str, value: Int32) -> None:
        super().__init__(value)
        self.label = label

    def describe(self) -> str:
        return self.label


# Test with generic parent
lc = LabeledContainer("count", 42)
print(lc.label)
print(lc.get())
print(lc.describe())
