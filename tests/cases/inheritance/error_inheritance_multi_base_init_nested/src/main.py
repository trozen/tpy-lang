# Base __init__ calls must be the leading statements of the child's __init__
# (LANGUAGE_FEATURES.md multi-base "Key points"): one nested in control flow
# is not, so it is rejected -- C++ cannot construct a base conditionally.
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
    def __init__(self, a: int32, b: int32, flag: bool) -> None:
        Left.__init__(self, a)
        if flag:
            Right.__init__(self, b)  # tpyc: error(/Right\.__init__\(self, \.\.\.\) must be the first statement in __init__/)
        else:
            Right.__init__(self, int32(0))
