# An `Own[container]` param as a single-yield generator's for-head iterable: the
# skeleton picks its iteration strategy off the un-unwrapped binding, so the
# payload's begin/end is not what it would spell.
from typing import Iterator
from tpy import Int32, Own


def drain(xs: Own[list[Int32]]) -> Iterator[Int32]:  # tpyc: error(/sgen\.iterable_own_binding/)
    for x in xs:
        yield x + len(xs)


def main() -> None:
    for u in drain([1, 2, 3]):
        print(u)


main()
