from tpy import Int32, readonly
from typing import Sequence


@readonly
def bad(seq: Sequence[Int32]) -> Int32:
    return seq.__len__()  # tpyc: error(/Call to non-readonly or unknown-effect method '__len__' is not allowed in @readonly function/)


print(0)
