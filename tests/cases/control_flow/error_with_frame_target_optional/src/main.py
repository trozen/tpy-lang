# A generator `with` whose target lives in the frame and whose `__enter__`
# returns an Optional: the frame field takes the plain write but its reads must
# deref the engaged optional, a binding kind the with-bind arm does not register.
from typing import Iterator
from tpy import int32


class OCM:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32 | None:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[int32]:
    with OCM(5) as x:  # tpyc: error(/not yet supported.*with.frame_target_family/)
        pass
    yield 1
    if x is not None:
        yield x


def main() -> None:
    for v in gen():
        print(v)


main()
