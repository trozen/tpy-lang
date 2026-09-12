# Own[<exposed class>] is not a valid boundary type: the host keeps its
# reference, so ownership can't transfer. Use the borrow form.
# tpy: ext_module
from tpy import int64, Own
from tpy.extern import export


@export
class C:
    def __init__(self, a: int64):
        self.a = a


@export
def take(c: Own[C]) -> int64:  # tpyc: error(/Own\[C\].*not a valid boundary type/)
    return c.a
