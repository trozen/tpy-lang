# Tuple-form isinstance with one valid and one invalid member -- the
# diagnostic cites the invalid member only.
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: int
    def __init__(self, y: int) -> None:
        self.y = y

class C:
    z: int
    def __init__(self, z: int) -> None:
        self.z = z


def f(v: A | B) -> None:
    if isinstance(v, (A, C)):  # tpyc: error(/'C' is not a member of union/)
        print("ac")
