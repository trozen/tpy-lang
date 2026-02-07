# Test: docstring before super().__init__() is allowed

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    extra: int

    def __init__(self, value: int, extra: int) -> None:
        """Initialize Child with value and extra."""
        super().__init__(value)
        self.extra = extra


c = Child(10, 20)
print(c.value)
print(c.extra)
