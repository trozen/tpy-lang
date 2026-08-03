# A DELEGATING manager: `__enter__` hands back an object that is not part of the
# manager, so the target aliasing it says nothing about how long the manager must
# live. Keeping the manager alive anyway would delay its __del__ past the block,
# where CPython drops it as soon as the `with` releases its reference -- visible
# here as the ordering of "wrapper dropped" against the yields.
#
# The self-returning sibling (with_target_aliases_manager) is the other half: there
# the target DOES alias the manager, so the manager has to be kept.
#
# The observable here is the __del__ ordering, not copy-vs-alias: a delegated
# target is a const alias, so it cannot be mutated to probe the boundary.
from typing import Iterator

from tpy import Int32


class Sentinel:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


SHARED: Sentinel = Sentinel(1)


class Wrapper:
    def __enter__(self) -> Sentinel:
        return SHARED

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        print("wrapper dropped")


def gen() -> Iterator[Int32]:
    with Wrapper() as g:
        pass
    yield 1
    yield g.n


def main() -> None:
    for v in gen():
        print(v)
    print(SHARED.n)


main()
