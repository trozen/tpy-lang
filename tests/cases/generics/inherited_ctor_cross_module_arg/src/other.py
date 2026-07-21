from tpy import Int32, ValueType


class Key(ValueType):
    x: Int32

    def __init__(self, x: Int32 = 0):
        self.x = x

    def __eq__(self, other: "Key") -> bool:
        return self.x == other.x
