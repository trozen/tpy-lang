# Generator on the resumable-frame path with an explicit bare `return`
# (stop iteration) before the body ends -- exercises the resumable
# generator return-lowering (_make_generator_resumable_return), distinct
# from the fall-off-end path.
from typing import Iterator
from tpy import Int32


def g() -> Iterator[Int32]:
    yield 1
    yield 2
    return


def main() -> None:
    for v in g():
        print(v)


main()
