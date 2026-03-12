# @readonly_propagate cannot be combined with @readonly, @staticmethod, or on __init__.
# Each invalid combination causes a parse error (only first error is emitted).
from tpy import Int32, Span, readonly, readonly_propagate

class Bad:
    @readonly_propagate  # tpyc: error(/cannot be combined with @readonly/)
    @readonly
    def m1(self) -> Span[Int32]: ...

    @readonly_propagate
    @staticmethod
    def m2() -> Span[Int32]: ...

    @readonly_propagate
    def __init__(self) -> None: ...

def main() -> None:
    pass
