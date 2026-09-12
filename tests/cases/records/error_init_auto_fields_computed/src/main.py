# Auto-declare only works for self.field = param, not computed expressions
from tpy import int32


class Baz:
    def __init__(self, x: int32):
        self.x = x
        self.doubled = x + x  # tpyc: error(/no field/)
