# isinstance with wrong argument count
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

def f(v: A | B) -> None:
    isinstance(v)  # tpyc: error(/exactly 2 arguments/)
