# A subscript read off an `Own[bytearray]` PARAM: the bytearray row declines the
# Own param slot's bare-name read rather than widening a second axis, so
# `b[0]` still rejects.
from tpy import Own


def first(b: Own[bytearray]) -> None:
    print(b[0])  # tpyc: error(/subscript.recv_type/)


def main() -> None:
    first(bytearray(b"ab"))


main()
