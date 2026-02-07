# Error: super() in class without parent

class Standalone:
    value: int

    def __init__(self, value: int) -> None:
        super().__init__()  # tpyc: error(/super\(\) requires a parent class/)
        self.value = value
