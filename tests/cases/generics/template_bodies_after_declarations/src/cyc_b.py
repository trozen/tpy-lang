# The other cycle member.
from tpy import int32, ValueType
import cyc_a


def twice(n: int32) -> int32:
    return n * 2 + cyc_a.base()


# A value type: passed by value, copy semantics intended.
class Val(ValueType):
    payload: int32

    def __init__(self, p: int32) -> None:
        self.payload = p


class Item:
    def __init__(self, n: int32) -> None:
        self.n = n
