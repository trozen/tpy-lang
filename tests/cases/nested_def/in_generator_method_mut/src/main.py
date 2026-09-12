# A nested def in a generator METHOD mutating self across yields: the
# frame must classify non-const and the mutation is visible on the
# caller's object after iteration.
from typing import Iterator
from tpy import int32


class Tally:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def steps(self, k: int32) -> Iterator[int32]:
        def push(v: int32) -> None:
            self.total += v

        for i in range(k):
            push(i + 1)
            yield self.total


def main() -> None:
    t = Tally()
    for v in t.steps(3):
        print(v)
    print(t.total)


main()
