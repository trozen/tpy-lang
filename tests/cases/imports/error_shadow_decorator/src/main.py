# Decorator shadowed by local def -- @readonly not recognized
from tpy import readonly, Int32

def readonly() -> int:
    return 0

class Foo:
    value: Int32
    @readonly  # tpyc: error(/Unknown decorator/)
    def get_value(self) -> Int32:
        return self.value
