# A local copied from a Literal parameter keeps its set; the refusal of an
# out-of-set rebinding names the base annotation, which takes it. The
# refusal itself is the false reject BUGS.md#literal-local-copy-keeps-set.
from typing import Literal


def f(x: Literal[2, 4]) -> int:
    y = x
    y = 3  # tpyc: error(/no common type; annotate its first binding: y: int32 = /)
    return y


print(f(2))
