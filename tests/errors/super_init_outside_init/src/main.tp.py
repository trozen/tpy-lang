# Error: super().__init__() called outside __init__

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    def __init__(self, value: int) -> None:
        super().__init__(value)

    def reset(self) -> None:
        super().__init__(0)  # tpyc: error(/super\(\)\.__init__\(\) can only be called inside __init__/)
