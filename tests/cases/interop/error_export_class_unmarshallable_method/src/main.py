# An @export class with a method whose param/return type doesn't marshal is
# rejected (every public method becomes a bound wrapper).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Bad:
    def __init__(self, n: Int64):
        self.n = n

    def frob(self, items: list[Int64]) -> None:  # tpyc: error(/method 'frob'.*parameter 'items'.*not yet marshallable/)
        pass
