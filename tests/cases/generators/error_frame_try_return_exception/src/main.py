# `except StopIteration` over an @error_return call inside a generator, where
# the `try` has to become states of its own (the `break` leaves it): the
# handler can only be entered by a failing unwrap, and the resumable frame has
# no edge for that. Rejected instead of emitting a catch nothing reaches
# (BUGS.md#frame-try-next-error-return-miscompiles) -- the restriction
# docs/LANGUAGE_FEATURES.md states on the generator try/except row and in the
# `__next__`/StopIteration iterator rules.
from typing import Iterator
from tpy import int32, error_return


class Src:
    n: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.n = 0
        self.limit = limit

    def __iter__(self) -> "Src":
        return self

    @error_return(StopIteration)
    def __next__(self) -> int32:
        if self.n >= self.limit:
            raise StopIteration
        v = self.n
        self.n += 1
        return v


def drain(it: Iterator[int32]) -> Iterator[int32]:
    while True:
        # The subject: the handler's `break` leaves the try, so the try is
        # decomposed into frame states.
        try:  # tpyc: error(/ReturnException/)
            x = next(it)
        except StopIteration:
            break
        yield x


def main() -> None:
    src = Src(3)
    for v in drain(iter(src)):
        print(v)


main()
