# Error: multiple super().__init__() calls

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    def __init__(self, value: int) -> None:
        super().__init__(value)
        super().__init__(value)  # tpyc: error(/super\(\)\.__init__\(\) can only be called once/)
