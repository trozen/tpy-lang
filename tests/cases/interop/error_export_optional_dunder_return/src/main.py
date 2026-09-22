# The dunder-slot rule covers the RETURN leg too: `__getitem__ -> Optional[T]`
# would already route through the shared return ladder, but a slot pins its
# shapes, so it is refused with the rule named until slots admit the gate
# one by one (TODO "Optional boundary -- deferred slices").
# tpy: ext_module
from typing import Optional
from tpy import int32
from tpy.extern import export


@export
class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __getitem__(self, k: int32) -> Optional[int32]:  # tpyc: error(/return type is 'int32 \| None', and an Optional does not cross a dunder slot/)
        if k < 0:
            return None
        return k
