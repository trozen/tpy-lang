# Storing a CONTAINER into `Any` has no lowering row. Two flavors hide behind
# the one reject: at a LAST use (this one -- `xs` is dead afterwards) moving the
# list into `Any` would preserve every observable behavior, so that half is an
# implementation gap; at a live use CPython aliases where `Any`'s owned storage
# would copy, which is a language question about what `Any` holds. Until the
# move flavor has a row, both stay a located error.
from typing import Any
from tpy import int32


def f() -> bool:
    xs: list[int32] = [1, 2]
    a: Any = xs  # tpyc: error(/expr\.coerce/)
    return a is None


def main() -> None:
    print(f())


main()
