from tpy import int32, ValueType


class Key(ValueType):
    x: int32

    def __init__(self, x: int32 = 0):
        self.x = x

    def __eq__(self, other: "Key") -> bool:
        return self.x == other.x
