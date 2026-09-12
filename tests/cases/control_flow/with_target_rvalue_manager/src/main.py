# An OWNED (rvalue) manager whose target aliases it and is read after a LATER
# suspension. The manager is the target's backing storage, so it has to live in
# the frame too -- a per-invocation local would leave the alias dangling and the
# post-resume read would return garbage rather than fail to build. __exit__
# observes the mutation made through the target, pinning the aliasing as well.
from typing import Iterator

from tpy import int32


class Counter:
    def __init__(self, start: int32):
        self.n = start

    def __enter__(self) -> "Counter":
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("exit sees", self.n)


def steps(limit: int32) -> Iterator[int32]:
    with Counter(limit) as guard:
        guard.n += 1
    yield 1
    yield guard.n


def main() -> None:
    for v in steps(5):
        print(v)


main()
