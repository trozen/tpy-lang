from tpy import ValueType, int32
import a


class Val(ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n
