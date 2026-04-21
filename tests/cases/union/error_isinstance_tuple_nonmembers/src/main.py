# Multi-non-member form in the tuple reports all offending types.
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

class D:
    w: int
    def __init__(self, w: int) -> None:
        self.w = w


def f(v: A | B) -> None:
    if isinstance(v, (C, D)):  # tpyc: error(/Types 'C', 'D' are not members of union/)
        print("cd")
