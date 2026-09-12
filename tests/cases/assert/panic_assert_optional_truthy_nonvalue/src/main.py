from tpy import int32


class C:
    v: int32

    def __init__(self, v: int32):
        self.v = v


x: C | None = None
assert x
