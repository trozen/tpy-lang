# A subscript read off an `Own[bytearray]` PARAM lowers (its `__getitem__` is
# the element access the bytearray family renders); the call handing the param
# a new bytearray is not lowered yet (BUGS.md#own-bytearray-param-call-rejects).
from tpy import Own


def first(b: Own[bytearray]) -> None:
    print(b[0])


def main() -> None:
    first(bytearray(b"ab"))  # tpyc: error(/call\.callee_kind/)


main()
