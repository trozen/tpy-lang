# Base __init__ calls must be top-level statements in the child's __init__.
# Nesting them in control flow is rejected with a targeted error message; the
# C++ member initializer list can't model conditional base construction.
from tpy import Int32


class Left:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class Right:
    b: Int32

    def __init__(self, b: Int32) -> None:
        self.b = b


class Child(Left, Right):
    def __init__(self, a: Int32, b: Int32, flag: bool) -> None:  # tpyc: error(/nested call\(s\) for: Right/)
        Left.__init__(self, a)
        if flag:
            Right.__init__(self, b)
        else:
            Right.__init__(self, Int32(0))
