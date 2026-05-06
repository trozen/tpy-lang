from b import B  # tpyc: error(/concretely inherits/)
from tpy import Int32

class A(B):
    extra: Int32
    def __init__(self, n: Int32, e: Int32) -> None:
        B.__init__(self, n)
        self.extra = e
