# isinstance with a variable (not a type) as second argument
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

def f(v: A | B) -> None:
    t: int = 1
    isinstance(v, t)  # tpyc: error(/must be a type/)
