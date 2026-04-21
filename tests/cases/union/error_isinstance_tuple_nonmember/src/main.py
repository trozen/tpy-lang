# Tuple-form isinstance with a type not in the declared union is rejected,
# just like the single-type form.
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
