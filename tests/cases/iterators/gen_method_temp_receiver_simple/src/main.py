# Regression: a SIMPLE (lambda-peephole) generator method on a temporary
# receiver. The peephole captures `[this]`, so the temporary must be lifted
# the same way the resumable frame's `const Box&` capture requires.
from typing import Iterator


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def each(self) -> Iterator[int]:
        for i in range(self.n):
            yield i * 10


def main() -> None:
    for v in Counter(3).each():
        print(v)


main()
