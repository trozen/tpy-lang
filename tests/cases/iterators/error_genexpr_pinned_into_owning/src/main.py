# A generator expression whose source is a combinator that OWNS a user-iterable
# temporary with a separate iterator (pinned: BUGS.md#separate-iter-temp-no-flush-slot),
# fed to another lazy combinator, which would move it: a located reject, not a C++ build error.
from tpy import int32, Own


class Cur:
    i: int32
    n: int32

    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n

    def __iter__(self) -> "Cur":
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Noisy:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Own[Cur]:
        return Cur(self.n)


def total(xs: list[int32]) -> int32:
    # `enumerate` moves the inner genexpr's frame into its own storage; the
    # frame holds a `zip` that owns `Noisy(2)` in place, with its iterator
    # already made.
    return sum(i + s for i, s in enumerate(a * b for a, b in zip(Noisy(2), xs)))  # tpyc: error(/genexpr.pinned_into_owning/)


def main() -> None:
    print(total([10, 20, 30]))


main()
