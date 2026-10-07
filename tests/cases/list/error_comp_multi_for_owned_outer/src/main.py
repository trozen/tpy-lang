# Only the innermost `for` clause of a comprehension may iterate a source that
# hands its elements over (`Own[T]`): an outer clause over one is refused.
from typing import Iterator
from tpy import Own


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def batches(n: int) -> Iterator[Own[list[P]]]:
    for i in range(n):
        yield [P(i), P(i + 1)]


def main() -> None:
    print([p.v for ps in batches(2) for p in ps])  # tpyc: error(/a .for. clause over a source that yields owned values must be the last clause of the comprehension/)


main()
