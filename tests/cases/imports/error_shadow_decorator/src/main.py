# Decorator shadowed by local def -- @readonly not recognized
from tpy import readonly, int32

def readonly() -> int:
    return 0

class Foo:
    value: int32
    @readonly  # tpyc: error(/Unknown decorator/)
    def get_value(self) -> int32:
        return self.value
