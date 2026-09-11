# A SIMPLE generator delegating to a cross-module generator call: the lambda
# __src init-capture deduces the callee's type, so no struct name is spelled.
# The resumable path spells one, and rejects only a SIMPLE callee -- see
# error_gen_delegate_cross_module_simple.
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
