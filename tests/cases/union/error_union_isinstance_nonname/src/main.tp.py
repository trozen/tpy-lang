# isinstance with non-name expression as first argument
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

def f(v: A | B) -> None:
    isinstance(A(1), A)  # tpyc: error(/must be a variable name/)
