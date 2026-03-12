# Error: @readonly_alt cannot be combined with @staticmethod.
from tpy import Int32, Span, readonly_alt

class Bad:
    @readonly_alt  # tpyc: error(/cannot be combined with @staticmethod/)
    @staticmethod
    def make() -> Span[Int32]: ...

def main() -> None:
    pass
