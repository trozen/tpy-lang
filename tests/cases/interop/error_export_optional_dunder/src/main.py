# An Optional operand does not cross a dunder SLOT: a slot pins its operand
# and return shapes (CPython supplies the operands directly, the emit binds
# each through the bare value marshaller), so the None gate the plain
# function/method boundary admits is refused here with the rule named --
# a slot that reached codegen would build a .so that fails at import.
# tpy: ext_module
from typing import Optional
from tpy import int32
from tpy.extern import export


@export
class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __getitem__(self, k: Optional[int32]) -> int32:  # tpyc: error(/parameter 'k' is 'int32 \| None', and an Optional does not cross a dunder slot/)
        if k is None:
            return -1
        return k
