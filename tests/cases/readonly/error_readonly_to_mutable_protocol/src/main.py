# A readonly[list[T]] cannot be passed where a MutableSequence is expected.
# The readonly protocol targets that DO accept it -- Sized and Sequence -- are
# pinned by tests/cases/readonly/readonly_to_readonly_protocol.
from tpy import Int32, readonly
from typing import MutableSequence

def mutate_first(xs: MutableSequence[Int32]) -> None:
    xs[0] = Int32(99)

def bad(items: readonly[list[Int32]]) -> None:
    mutate_first(items)  # tpyc: error(/readonly/)
