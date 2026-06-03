# A static-protocol parameter aliased into a local and then iterated across a
# suspension has no concrete C++ backing in the resumable frame (only captured
# params carry the deduced template arg T_<pname>). It is rejected cleanly at
# frame-field emit rather than producing a broken C++ build. Iterating the
# parameter directly is the supported form.
from typing import Iterator, Iterable


def echo(it: Iterable[int]) -> Iterator[int]:  # tpyc: error(/protocol type aliasing a protocol-typed parameter/)
    xs = it
    for x in xs:
        yield x
        yield x


def main() -> None:
    for v in echo([1, 2]):
        print(v)


main()
