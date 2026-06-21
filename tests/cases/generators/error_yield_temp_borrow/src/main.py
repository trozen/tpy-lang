# Yielding a borrow of a fresh temporary stays rejected (it would dangle) -- the
# frame-resident-local exemption must not leak to non-rooted values.
from typing import Iterator


def yield_literal() -> Iterator[list[int]]:
    yield [1, 2, 3]   # tpyc: error(/Cannot yield local or temporary as reference/)
