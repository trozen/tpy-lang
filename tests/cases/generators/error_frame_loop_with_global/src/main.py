# In a generator body, a `with` runs its manager's __enter__ and __exit__,
# which may write a module global a generator held into the next pass
# borrows.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


GL: list[list[int32]] = [[1, 2]]


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


class Grower:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> "Grower":
        GL.append([3])
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def outer(n: int32) -> Iterator[int32]:
    m = Grower()
    for i in range(n):
        # The subject: entering the block grows GL.
        with m:
            pass
        g = items(GL[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls '__enter__', which may write the module global 'GL'/)
        for v in g:
            yield v
            break


print(list(outer(3)))
