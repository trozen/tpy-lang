# Tuple-form isinstance requires all elements to be type names.
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y


def f(v: A | B) -> None:
    if isinstance(v, (A, 42)):  # tpyc: error(/tuple elements must be type names/)
        print("ab")
