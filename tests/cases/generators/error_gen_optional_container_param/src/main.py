# The shape adjacent to a container frame param: an OPTIONAL container param.
# `_optional_ptr_borrow` stays narrow (F1 records only) because many
# binding-level consumers key F1 renders on it, so the optional flavour keeps
# its own rung even though the bare bytearray param now rides the axis.
from typing import Iterator, Optional
from tpy import int32


def twice(b: Optional[bytearray]) -> Iterator[int32]:  # tpyc: error(/not yet supported.*res.param_type/)
    yield 1
    yield 2


def main() -> None:
    for got in twice(bytearray(b"a")):
        print(got)


main()
