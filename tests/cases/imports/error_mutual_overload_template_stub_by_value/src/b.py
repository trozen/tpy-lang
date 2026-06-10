# Cycle peer: defines the by-value record B and imports back from a to close
# the a <-> b import cycle.
from a import helper
from tpy import Int32, ValueType


class B(ValueType):
    payload: Int32
    def __init__(self, p: Int32) -> None:
        self.payload = p


def use_helper() -> Int32:
    return helper()
