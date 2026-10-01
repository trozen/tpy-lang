# A field keeps its declared type: an aug-assign whose result is wider than
# the field is refused
from tpy import int32, int64


class Acc:
    n32: int32

    def __init__(self) -> None:
        self.n32 = 0

    def add(self, a64: int64) -> None:
        # the int64 result into the int32 field
        self.n32 += a64  # tpyc: error(/Type mismatch in '\+=' to 'self\.n32': expected int32, got int64/)


Acc().add(1)
