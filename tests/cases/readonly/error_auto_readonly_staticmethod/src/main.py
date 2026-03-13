# Error: @auto_readonly cannot be combined with @staticmethod.
from tpy import Int32, Span, auto_readonly

class Bad:
    @auto_readonly  # tpyc: error(/cannot be combined with @staticmethod/)
    @staticmethod
    def make() -> Span[Int32]: ...

def main() -> None:
    pass
