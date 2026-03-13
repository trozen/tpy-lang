# @auto_readonly cannot be combined with @readonly, @staticmethod, or on __init__.
# Each invalid combination causes a parse error (only first error is emitted).
from tpy import Int32, Span, readonly, auto_readonly

class Bad:
    @auto_readonly  # tpyc: error(/cannot be combined with @readonly/)
    @readonly
    def m1(self) -> Span[Int32]: ...

    @auto_readonly
    @staticmethod
    def m2() -> Span[Int32]: ...

    @auto_readonly
    def __init__(self) -> None: ...

def main() -> None:
    pass
