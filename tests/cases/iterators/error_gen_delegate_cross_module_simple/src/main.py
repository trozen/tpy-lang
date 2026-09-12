# The one cross-module delegation still rejected: a callee that is a SIMPLE
# generator. Its own module already emitted it as an unnameable lambda
# wrapper, and the pre-scan that promotes such a callee to a named struct
# only sees this module. Rejected rather than emitting ill-formed C++.
from typing import Iterator
from tpy import int32
from gensrc import walk


def gen() -> Iterator[int32]:
    yield 100
    for x in walk():  # tpyc: error(/single yield in a loop/)
        yield x


def main() -> None:
    for v in gen():
        print(v)


main()
