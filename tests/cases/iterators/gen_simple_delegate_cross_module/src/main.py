# A single-yield generator delegating to a cross-module generator call; the
# two-yield twin is gen_resumable_delegate_cross_module.
from typing import Iterator
from tpy import int32
from itersrc import walk


def g() -> Iterator[int32]:
    for x in walk():  # tpyc: ok
        yield x


def main() -> None:
    for v in g():
        print(v)


main()
