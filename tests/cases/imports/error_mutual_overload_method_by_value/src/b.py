from a import helper
from tpy import int32, ValueType

class B(ValueType):
    payload: int32
    def __init__(self, p: int32) -> None:
        self.payload = p

def double() -> int32:
    return helper() * 2
