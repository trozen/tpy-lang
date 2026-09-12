# Cycle peer: defines the by-value record B and imports back from a to close
# the a <-> b import cycle.
from a import helper
from tpy import int32, ValueType


class B(ValueType):
    payload: int32
    def __init__(self, p: int32) -> None:
        self.payload = p


def use_helper() -> int32:
    return helper()
