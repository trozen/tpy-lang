# Own[<exposed class>] is not a valid boundary type: the host keeps its
# reference, so ownership can't transfer. Use the borrow form.
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export


@export
class C:
    def __init__(self, a: Int64):
        self.a = a


@export
def take(c: Own[C]) -> Int64:  # tpyc: error(/Own\[C\].*not a valid boundary type/)
    return c.a
