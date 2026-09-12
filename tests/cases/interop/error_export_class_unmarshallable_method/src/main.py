# An @export class with a method whose param/return type doesn't marshal is
# rejected (every public method becomes a bound wrapper). bytearray shares
# bytes' C++ repr but is a mutable reference type, so it stays inadmissible
# (containers now cross at the method boundary; bytearray still doesn't).
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Bad:
    def __init__(self, n: int64):
        self.n = n

    def frob(self, items: bytearray) -> None:  # tpyc: error(/method 'frob'.*parameter 'items'.*cannot cross the CPython boundary/)
        pass
