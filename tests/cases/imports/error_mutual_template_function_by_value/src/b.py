from a import helper
from tpy import Int32, ValueType

class B(ValueType):
    payload: Int32
    def __init__(self, p: Int32) -> None:
        self.payload = p

def make_b() -> B:
    return B(helper())
