# Error: two super().__init__() calls initialize the same base twice

class Parent:
    value: int

    def __init__(self, value: int) -> None:
        self.value = value


class Child(Parent):
    def __init__(self, value: int) -> None:
        super().__init__(value)
        super().__init__(value)  # tpyc: error(/'Parent' is initialized twice in 'Child\.__init__' \('super\(\)\.__init__\(\)' already initializes it\)/)
