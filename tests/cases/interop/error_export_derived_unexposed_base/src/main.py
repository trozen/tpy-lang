# An @export class inheriting a non-exposed base is rejected: the base has no
# CPython type to wire as tp_base (an exposed class can only inherit an
# exposed class).
# tpy: ext_module
from tpy import int64
from tpy.extern import export


class Plain:
    x: int64

    def __init__(self, x: int64):
        self.x = x


@export
class Derived(Plain):  # tpyc: error(/base class 'Plain' is not exposed/)
    def get(self) -> int64:
        return self.x
