# A local copied from a Literal parameter keeps its set (the false reject
# BUGS.md#literal-local-copy-keeps-set); when the set's base cannot hold the
# out-of-set value either, the refusal names the set's values instead.
from typing import Literal
from tpy import uint64


def f(x: Literal[2, 4], u: uint64) -> uint64:
    y = x
    y = u  # tpyc: error(/no common type; store one of 2, 4/)
    return y


print(f(2, 3))
