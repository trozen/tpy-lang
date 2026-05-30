# Regression: a generator method on a temporary receiver of a GENERIC record.
# The lifted temp must be declared with the instantiated type (Holder<BigInt>),
# not the bare template name.
from typing import Iterator


class Holder[T]:
    items: list[T]

    def __init__(self, items: list[T]) -> None:
        self.items = items

    def walk(self) -> Iterator[T]:
        for x in self.items:
            yield x


def main() -> None:
    for v in Holder([1, 2, 3]).walk():
        print(v)


main()
