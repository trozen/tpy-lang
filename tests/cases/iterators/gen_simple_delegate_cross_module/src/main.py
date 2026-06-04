# A SIMPLE generator delegating to a cross-module generator call: the lambda
# __src init-capture deduces the callee's type, so cross-module works on the
# simple path (the resumable path rejects it -- see error_gen_delegate_cross_module).
from typing import Iterator
from tpy import Int32
from itersrc import walk


def g() -> Iterator[Int32]:
    for x in walk():  # tpyc: ok
        yield x


def main() -> None:
    for v in g():
        print(v)


main()
