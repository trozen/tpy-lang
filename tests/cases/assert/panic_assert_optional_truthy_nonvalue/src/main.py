from tpy import Int32


class C:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


x: C | None = None
assert x
