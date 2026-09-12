# When multiple bases define __init__, the child must call every one
# explicitly. Missing one is an error.
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
    def __init__(self, a: int32, b: int32) -> None:  # tpyc: error(/missing calls for: Right/)
        Left.__init__(self, a)
