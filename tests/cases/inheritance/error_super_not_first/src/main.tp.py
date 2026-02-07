# Error: super().__init__() must be the first statement

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    other: int

    def __init__(self, value: int, other: int) -> None:
        self.other = other  # This is first, not super().__init__()
        super().__init__(value)  # tpyc: error(/super\(\)\.__init__\(\) must be the first statement/)
