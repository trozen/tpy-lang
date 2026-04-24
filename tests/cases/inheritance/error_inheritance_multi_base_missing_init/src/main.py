# When multiple bases define __init__, the child must call every one
# explicitly. Missing one is an error.
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
    def __init__(self, a: Int32, b: Int32) -> None:  # tpyc: error(/missing calls for: Right/)
        Left.__init__(self, a)
