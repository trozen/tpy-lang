# Two-yield generator method with its own type param `[U]` on a
# non-generic class. Two instantiations exercise template monomorphization.
from typing import Iterator


class Foo:
    def items[U](self, x: U) -> Iterator[U]:
        yield x
        yield x


def main() -> None:
    f = Foo()
    for v in f.items(42):
        print(v)
    for s in f.items("hi"):
        print(s)


main()
