# Delegating to a CROSS-MODULE generator call inside a resumable generator is
# rejected cleanly: the callee's struct type is not spellable across the
# module boundary.
from typing import Iterator
from tpy import Int32
from itersrc import walk


def gen() -> Iterator[Int32]:
    yield 0
    for x in walk():  # tpyc: error(/same module/)
        yield x


def main() -> None:
    for v in gen():
        print(v)


main()
