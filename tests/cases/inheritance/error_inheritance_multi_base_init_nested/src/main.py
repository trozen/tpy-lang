# Base __init__ calls must be top-level statements in the child's __init__.
# Nesting them in control flow is rejected with a targeted error message; the
# C++ member initializer list can't model conditional base construction.
from tpy import int32


class Left:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class Right:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class Child(Left, Right):
    def __init__(self, a: int32, b: int32, flag: bool) -> None:  # tpyc: error(/nested call\(s\) for: Right/)
        Left.__init__(self, a)
        if flag:
            Right.__init__(self, b)
        else:
            Right.__init__(self, int32(0))
