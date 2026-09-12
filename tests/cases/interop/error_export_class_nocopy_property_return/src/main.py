# A property getter returning a @nocopy exposed class hits the same located
# reject as a method return (property accessor returns carry the bare class
# type -- no RefType normalization -- so the reject must peel to the
# boundary inner).
# tpy: ext_module
from tpy import int64, nocopy
from tpy.extern import export


@export
@nocopy
class Res:
    n: int64

    def __init__(self, n: int64):
        self.n = n

    @property
    def itself(self) -> "Res":  # tpyc: error(/property 'itself' is a @nocopy class 'Res' returned by reference/)
        return self
